"""Scan-and-bill: barcode scans turned into sales or purchase rows.

A bill is a list of scanned lines. Each line becomes one SalesEntry (or
PurchaseEntry) row, and every row of the same bill carries the same
`bill_no`, so the Day Book still reads plain rows while a bill can be
shown together. Everything is validated before the first row is written.
"""
import datetime
import json
import secrets
import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.organizations.tenant_context import get_current_organization

from apps.organizations.levels import org_levels, split_levels

from .models import (
    Category, CommonOption, PaymentMode, Product, PurchaseEntry, SalesEntry, StockUnit, Subcategory,
)

# Letters and digits a person can read off a label without confusing O/0 or I/1.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PRODUCT_PREFIX = "P"
CATEGORY_PREFIX = "C"
SUBCATEGORY_PREFIX = "S"


class BillError(Exception):
    """Raised when a bill or product can't be saved. `fields` maps a field
    name (or "lines") to a message, the same shape the form errors use."""

    def __init__(self, message: str, fields: dict | None = None):
        super().__init__(message)
        self.message = message
        self.fields = fields or {}


def find_product(barcode: str) -> Product | None:
    barcode = (barcode or "").strip()
    if not barcode:
        return None
    return Product.objects.filter(barcode=barcode, is_active=True).first()


def code_taken(code: str, *, exclude=None) -> bool:
    """Whether any product, category or sub-category already uses this code.
    `exclude` is the row being edited, so it doesn't clash with itself."""
    for model in (Product, Category, Subcategory):
        for row in model.objects.filter(barcode=code):
            if exclude is None or row.pk != exclude.pk:
                return True
    return False


def new_code(prefix: str) -> str:
    """A fresh code for a label, e.g. P7K2M9QX. Checked against every code in use."""
    while True:
        code = prefix + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not code_taken(code):
            return code


def ensure_codes(model, prefix: str) -> int:
    """Gives every row of `model` that has no barcode one generated code. Returns how many were given one."""
    count = 0
    for row in model.objects.filter(barcode=""):
        row.barcode = new_code(prefix)
        row.save()
        count += 1
    return count


def resolve_code(code: str):
    """What a scanned code stands for: ("product", Product), ("subcategory", Subcategory),
    ("category", Category), or (None, None) when no active item uses it."""
    code = (code or "").strip()
    if not code:
        return None, None
    product = find_product(code)
    if product is not None:
        return "product", product
    unit = None
    if tracks_phones():
        unit = StockUnit.objects.filter(serial=normalize_serial(code)).first()
        if unit is None:
            unit = StockUnit.objects.filter(imei2=normalize_serial(code)).first()
    if unit is not None:
        return "unit", unit
    subcategory = Subcategory.objects.filter(barcode=code, is_active=True).first()
    if subcategory is not None:
        return "subcategory", subcategory
    category = Category.objects.filter(barcode=code, is_active=True).first()
    if category is not None:
        return "category", category
    return None, None


def _money(raw, field: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise BillError(f"{field} must be a number.", {field: f"{field} must be a number."})
    if value < 0:
        raise BillError(f"{field} can't be negative.", {field: f"{field} can't be negative."})
    return value.quantize(Decimal("0.01"))


def _quantity(raw) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise BillError("Quantity must be a whole number.", {"lines": "Quantity must be a whole number."})
    if value < 1:
        raise BillError("Quantity must be at least 1.", {"lines": "Quantity must be at least 1."})
    return value


def _opening(raw) -> int:
    """The opening quantity from a request: a whole number, 0 or more; blank means 0."""
    if raw in (None, ""):
        return 0
    try:
        value = int(str(raw).strip())
    except ValueError:
        message = "Opening quantity must be a whole number."
        raise BillError(message, {"opening_stock": message})
    if value < 0:
        message = "Opening quantity can't be negative."
        raise BillError(message, {"opening_stock": message})
    return value


def create_product(
    *, barcode: str, name: str, sale_price, purchase_price, category: Category | None,
    subcategory: Subcategory | None = None, attributes: dict | None = None, opening_stock=None,
    path=None, common=None,
) -> Product:
    if path:
        category, subcategory = ensure_path(path)
    barcode = (barcode or "").strip()
    name = (name or "").strip()
    if not name:
        raise BillError("Name is required.", {"name": "Name is required."})
    if not barcode:
        # No code on the packet: give the product its own printed label code.
        barcode = new_code(PRODUCT_PREFIX)
    elif code_taken(barcode):
        raise BillError(
            "This barcode is already used by a product, category or sub-category.",
            {"barcode": "This barcode is already used by a product, category or sub-category."},
        )
    if category is not None and category.kind != Category.Kind.PRODUCT:
        raise BillError("Pick a product category.", {"category": "Pick a product category."})
    product = Product(
        barcode=barcode,
        name=name[:150],
        category=category,
        subcategory=subcategory,
        **(attributes or {}),
        opening_stock=_opening(opening_stock),
        sale_price=_money(sale_price or 0, "sale_price"),
        purchase_price=_money(purchase_price or 0, "purchase_price"),
    )
    apply_common(product, common)
    product.save()
    return product


def update_product(
    product: Product, *, name, barcode, sale_price, purchase_price, category,
    subcategory=None, keep_subcategory=True, attributes: dict | None = None, opening_stock=None,
    path=None, common=None,
) -> Product:
    """Edits a saved product. A blank barcode keeps the current code; a new one must not clash with any other code."""
    if path is not None:
        # A path from the screen sets both the category and the sub-category (empty clears them).
        category, subcategory = ensure_path(path)
        keep_subcategory = False
    name = (name or "").strip()
    barcode = (barcode or "").strip() or product.barcode
    if not name:
        raise BillError("Name is required.", {"name": "Name is required."})
    if barcode != product.barcode and code_taken(barcode, exclude=product):
        message = "This barcode is already used by a product, category or sub-category."
        raise BillError(message, {"barcode": message})
    if category is not None and category.kind != Category.Kind.PRODUCT:
        raise BillError("Pick a product category.", {"category": "Pick a product category."})
    product.name = name[:150]
    product.barcode = barcode
    product.category = category
    if not keep_subcategory:
        product.subcategory = subcategory
    for field, value in (attributes or {}).items():
        setattr(product, field, value)
    apply_common(product, common)
    if opening_stock is not None:
        product.opening_stock = _opening(opening_stock)
    product.sale_price = _money(sale_price or 0, "sale_price")
    product.purchase_price = _money(purchase_price or 0, "purchase_price")
    product.save()
    return product


def _new_bill_no(kind: str, date: datetime.date) -> str:
    prefix = "S" if kind == "sale" else "P"
    return f"{prefix}{date:%y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def normalize_serial(raw) -> str:
    return str(raw or "").strip().upper()


# What can be recorded for a phone, with the longest text each field takes.
# "model" in a request is stored as model_name, "other" as other_details.
UNIT_DETAIL_FIELDS = {
    "brand": ("brand", 100),
    "model": ("model_name", 150),
    "colour": ("colour", 50),
    "ram": ("ram", 30),
    "storage": ("storage", 30),
    "imei2": ("imei2", 64),
    "other": ("other_details", 255),
}


def clean_details(raw, keep_blank: bool = False) -> dict:
    """The details sent for a phone as {model field: text}, trimmed and cut to length.
    With `keep_blank`, a field that was sent empty stays in the result (to clear it on an edit)."""
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, (field, limit) in UNIT_DETAIL_FIELDS.items():
        if keep_blank and key not in raw:
            continue
        value = str(raw.get(key) or "").strip()[:limit]
        if value or keep_blank:
            cleaned[field] = normalize_serial(value) if field == "imei2" else value
    return cleaned


def add_opening_unit(product: Product, serial, details=None) -> StockUnit:
    """Records a phone already in stock before the shop used the app: just its IMEI and details,
    with no purchase bill, so nothing is added to purchases or the books."""
    if not tracks_phones():
        message = "Phones are only recorded by IMEI for a mobiles organization."
        raise BillError(message, {"serial": message})
    serial = normalize_serial(serial)
    if not serial:
        raise BillError("Enter the phone's IMEI.", {"serial": "Enter the phone's IMEI."})
    if StockUnit.objects.filter(serial=serial).exists() or StockUnit.objects.filter(imei2=serial).exists():
        message = f"IMEI {serial} is already recorded."
        raise BillError(message, {"serial": message})
    cleaned = clean_details(details)
    _check_second_imei(cleaned, [serial])
    fields = {
        "brand": product.brand or (product.subcategory.name if product.subcategory_id else ""),
        "model_name": product.name,
        "colour": product.colour,
        "ram": product.ram,
        "storage": product.storage,
    }
    fields.update(cleaned)
    return StockUnit.objects.create(
        product=product, serial=serial, purchase_date=datetime.date.today(), **fields,
    )


def update_unit_details(unit, details) -> StockUnit:
    """Changes what is recorded for a phone: brand, model, colour, RAM, storage, second IMEI, notes.
    The IMEI itself and its status are never changed here."""
    cleaned = clean_details(details, keep_blank=True)
    imei2 = cleaned.get("imei2", "")
    if imei2:
        if imei2 == unit.serial:
            message = "The second IMEI is the same as the first."
            raise BillError(message, {"imei2": message})
        for other in list(StockUnit.objects.filter(serial=imei2)) + list(StockUnit.objects.filter(imei2=imei2)):
            if other.pk != unit.pk:
                message = f"IMEI {imei2} is already recorded."
                raise BillError(message, {"imei2": message})
    for field, value in cleaned.items():
        setattr(unit, field, value)
    unit.save()
    return unit


def unit_description(unit) -> str:
    """'Vivo · V10 · Blue · RAM 8GB · 128GB' from what was recorded for the phone."""
    parts = [
        unit.brand,
        unit.model_name or unit.product.name,
        unit.colour,
        f"RAM {unit.ram}" if unit.ram else "",
        unit.storage,
    ]
    return " · ".join(p for p in parts if p)


def inventory_type() -> str:
    """The current organization's inventory type: MOBILES, CLOTHES or OTHER."""
    return getattr(get_current_organization(), "inventory_type", "OTHER") or "OTHER"


def tracks_phones() -> bool:
    """Only a mobiles organization tracks each phone by its IMEI."""
    return inventory_type() == "MOBILES"


def is_tracked(product: Product) -> bool:
    """A product is tracked phone by phone once any unit of it has been recorded
    (and only for an organization that keeps mobiles)."""
    return tracks_phones() and StockUnit.objects.filter(product=product).exists()


# What an item can carry for a clothes organization. Request key -> model field and its longest text.
ITEM_ATTRIBUTES = {
    "brand": ("brand", 100),
    "size": ("size", 30),
    "colour": ("colour", 50),
    "ram": ("ram", 30),
    "storage": ("storage", 30),
    "extra": ("extra_details", 255),
}

# Which of those an organization's inventory type asks for, in the order they are shown.
ATTRIBUTES_BY_TYPE = {
    "MOBILES": ["brand", "colour", "ram", "storage", "extra"],
    "CLOTHES": ["brand", "size", "colour", "extra"],
    "OTHER": ["brand", "extra"],
}


def clean_attributes(data, keep_blank: bool = False) -> dict:
    """{model field: text} for the item attributes in a request, trimmed and cut to length.
    A blank is kept only with `keep_blank`, to clear a field on an edit."""
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, (field, limit) in ITEM_ATTRIBUTES.items():
        if key not in data:
            continue
        value = str(data.get(key) or "").strip()[:limit]
        if value or keep_blank:
            cleaned[field] = value
    return cleaned


def structure() -> list:
    """The current organization's category levels (the default three when none is set)."""
    return org_levels(get_current_organization())


def structure_parts():
    """(group levels, item level, common levels) of the current organization's structure."""
    return split_levels(structure())


# Common levels whose values live in a column of their own on the item.
LEGACY_COMMON_FIELDS = {
    "colour": "colour", "color": "colour", "ram": "ram", "storage": "storage", "size": "size", "brand": "brand",
}


def _stored_common(product: Product) -> dict:
    try:
        data = json.loads(product.common_values or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def common_values(product: Product) -> dict:
    """{common level name: value} for the values this item has, for the current structure."""
    _groups, _item, commons = structure_parts()
    stored = _stored_common(product)
    values = {}
    for level in commons:
        field = LEGACY_COMMON_FIELDS.get(level["name"].lower())
        value = getattr(product, field) if field else stored.get(level["name"], "")
        if value:
            values[level["name"]] = value
    return values


def remember_common(level: str, value: str) -> None:
    """Adds `value` to the shared list for a common level, if it is not there yet."""
    wanted = value.strip().lower()
    for option in CommonOption.objects.filter(level=level):
        if option.value.strip().lower() == wanted:
            return
    CommonOption(level=level, value=value.strip()).save()


def apply_common(product: Product, values) -> None:
    """Sets the item's common values from {level name: text}. A name that is not a common level of the
    structure is ignored; a blank clears that value. New values are added to the shared lists."""
    if not isinstance(values, dict):
        return
    _groups, _item, commons = structure_parts()
    names = {level["name"].lower(): level["name"] for level in commons}
    stored = _stored_common(product)
    for raw_name, raw_value in values.items():
        name = names.get(str(raw_name).strip().lower())
        if name is None:
            continue
        value = " ".join(str(raw_value or "").split())
        field = LEGACY_COMMON_FIELDS.get(name.lower())
        if field:
            setattr(product, field, value[: Product._meta.get_field(field).max_length])
        elif value:
            stored[name] = value[:100]
        else:
            stored.pop(name, None)
        if value:
            remember_common(name, value[:100])
    product.common_values = json.dumps(stored) if stored else ""


def common_options() -> dict:
    """{common level name: [values]} from the shared lists, for the current structure."""
    _groups, _item, commons = structure_parts()
    wanted = {level["name"] for level in commons}
    options = {name: [] for name in wanted}
    for option in CommonOption.objects.filter(is_active=True):
        if option.level in options:
            options[option.level].append(option.value)
    return {name: sorted(set(values), key=str.lower) for name, values in options.items()}


def common_option_rows() -> list:
    """The shared lists with their ids, for the Setup page: [{id, level, value}]."""
    _groups, _item, commons = structure_parts()
    wanted = {level["name"] for level in commons}
    rows = [
        {"id": str(o.pk), "level": o.level, "value": o.value}
        for o in CommonOption.objects.filter(is_active=True) if o.level in wanted
    ]
    return sorted(rows, key=lambda r: (r["level"].lower(), r["value"].lower()))


def add_common_option(level, value) -> CommonOption:
    """Adds a value to the shared list of a common level of the current structure."""
    _groups, _item, commons = structure_parts()
    names = {c["name"].lower(): c["name"] for c in commons}
    name = names.get(str(level or "").strip().lower())
    if name is None:
        message = "That is not a common level."
        raise BillError(message, {"level": message})
    value = " ".join(str(value or "").split())[:100]
    if not value:
        raise BillError("Enter a value.", {"value": "Enter a value."})
    remember_common(name, value)
    return next(o for o in CommonOption.objects.filter(level=name) if o.value.lower() == value.lower())


def product_path(product: Product) -> list:
    """The names from the top group down to the item's own group, e.g. ['Mobile', 'Apple']."""
    if product.subcategory_id:
        names, node = [], product.subcategory
        while node is not None:
            names.append(node.name)
            node = node.parent
        names.reverse()
        return [product.subcategory.category.name] + names
    return [product.category.name] if product.category_id else []


def ensure_path(names) -> tuple:
    """Finds, or creates, the group chain for a list of names (top group first) and returns
    (category, sub-category). The top name is a product category; each name below it is a
    sub-category inside the one before. Fewer names than levels is fine; blanks must be at the end."""
    groups, _item, _commons = structure_parts()
    cleaned = [" ".join(str(n or "").split()) for n in (names or [])]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if len(cleaned) > len(groups):
        message = f"This structure has {len(groups)} group level{'' if len(groups) == 1 else 's'} above the item."
        raise BillError(message, {"path": message})
    if any(not n for n in cleaned):
        message = "Fill the levels in order, from the top."
        raise BillError(message, {"path": message})
    if not cleaned:
        return None, None
    category = next(
        (c for c in Category.objects.filter(kind=Category.Kind.PRODUCT) if c.name.lower() == cleaned[0].lower()), None,
    )
    if category is None:
        category = Category(kind=Category.Kind.PRODUCT, name=cleaned[0][:100])
        category.save()
    node = None
    for name in cleaned[1:]:
        parent_id = node.pk if node else None
        found = next(
            (
                s for s in Subcategory.objects.filter(category=category)
                if s.parent_id == parent_id and s.name.lower() == name.lower()
            ),
            None,
        )
        if found is None:
            found = Subcategory(category=category, parent=node, name=name[:100])
            found.save()
        node = found
    return category, node


def path_options() -> list:
    """For each group level, the names already used there, to suggest while typing."""
    groups, _item, _commons = structure_parts()
    options = [set() for _ in groups]
    if not groups:
        return []
    categories = [c for c in Category.objects.filter(kind=Category.Kind.PRODUCT, is_active=True)]
    options[0] = {c.name for c in categories}
    category_ids = {c.pk for c in categories}
    subs = {s.pk: s for s in Subcategory.objects.filter(is_active=True) if s.category_id in category_ids}
    for sub in subs.values():
        depth, node = 1, sub
        while node.parent_id and node.parent_id in subs:
            depth += 1
            node = subs[node.parent_id]
        if depth < len(groups):
            options[depth].add(sub.name)
    return [sorted(names, key=str.lower) for names in options]


def structure_config() -> dict:
    """Everything an item screen needs about the structure: the levels, suggestions and shared lists."""
    groups, item, commons = structure_parts()
    return {
        "groups": [g["name"] for g in groups],
        "item": item["name"],
        "commons": [c["name"] for c in commons],
        "pathOptions": path_options(),
        "commonOptions": common_options(),
    }


def attributes_text(product: Product) -> str:
    """The item's common values as one line, e.g. 'Black · RAM 8GB · 128GB'. For an organization with no
    common levels it falls back to the item's brand, size, colour and notes."""
    _groups, _item, commons = structure_parts()
    if commons:
        values = common_values(product)
        parts = []
        for level in commons:
            value = values.get(level["name"])
            if not value:
                continue
            lower = level["name"].lower()
            parts.append(f"RAM {value}" if lower == "ram" else f"Size {value}" if lower == "size" else value)
        if product.extra_details:
            parts.append(product.extra_details)
        return " · ".join(parts)
    parts = [
        product.brand,
        f"Size {product.size}" if product.size else "",
        product.colour,
        f"RAM {product.ram}" if product.ram else "",
        product.storage,
        product.extra_details,
    ]
    return " · ".join(p for p in parts if p)


def units_in_stock(product: Product) -> int:
    return StockUnit.objects.filter(product=product, status=StockUnit.Status.IN_STOCK).count()


def _check_second_imei(details: dict, serials: list) -> None:
    imei2 = details.get("imei2", "")
    if not imei2:
        return
    if imei2 in serials:
        message = "The second IMEI is the same as the first."
        raise BillError(message, {"lines": message})
    if StockUnit.objects.filter(serial=imei2).exists() or StockUnit.objects.filter(imei2=imei2).exists():
        message = f"IMEI {imei2} is already recorded."
        raise BillError(message, {"lines": message})


def _check_new_serials(product: Product, serials: list) -> None:
    seen = set()
    for serial in serials:
        if serial in seen:
            raise BillError(f"IMEI {serial} is listed twice.", {"lines": f"IMEI {serial} is listed twice."})
        seen.add(serial)
        if StockUnit.objects.filter(serial=serial).exists() or StockUnit.objects.filter(imei2=serial).exists():
            message = f"IMEI {serial} is already recorded."
            raise BillError(message, {"lines": message})


def _check_units_for_sale(product: Product, serials: list) -> list:
    """The stock units being sold. Each must be this product and still in stock."""
    units = []
    for serial in serials:
        unit = StockUnit.objects.filter(serial=serial).first()
        if unit is None or unit.product_id != product.pk:
            message = f"IMEI {serial} is not a {product.name} in your stock."
            raise BillError(message, {"lines": message})
        if unit.status != StockUnit.Status.IN_STOCK:
            message = f"IMEI {serial} is already sold."
            raise BillError(message, {"lines": message})
        units.append(unit)
    return units


def save_bill(
    *,
    kind: str,
    date: datetime.date,
    payment_mode: str,
    bank_account,
    category: Category | None,
    vendor: str,
    lines: list,
    min_date: datetime.date | None,
    created_by_email: str,
    subcategory: Subcategory | None = None,
) -> dict:
    """Writes one sale or purchase bill. `category` is the revenue channel for
    a sale and the purchase category for a purchase. Each line is a dict with
    `product_id`, `quantity`, an optional `unit_price` (defaults to the product's
    sale or purchase price) and optional `serials` (IMEI or serial numbers).

    A line with serials becomes one row per phone, and the phones are tracked:
    a purchase records them in stock, and a sale marks them sold. A tracked
    product can only be sold by scanning its phones, not by the box barcode.
    Returns {"bill_no", "rows", "total"}."""
    if kind not in ("sale", "purchase"):
        raise BillError("type must be sale or purchase.", {"type": "type must be sale or purchase."})
    if min_date and date < min_date:
        message = f"Dates before {min_date:%d %b %Y} aren't allowed on your plan."
        raise BillError(message, {"date": message})
    if payment_mode not in PaymentMode.values:
        raise BillError("Pick a payment mode.", {"payment_mode": "Pick a payment mode."})
    if category is None:
        field = "channel" if kind == "sale" else "category"
        label = "Pick a revenue channel." if kind == "sale" else "Pick a purchase category."
        raise BillError(label, {field: label})
    if not lines:
        raise BillError("Scan at least one item.", {"lines": "Scan at least one item."})

    products = {str(p.pk): p for p in Product.objects.filter(is_active=True)}
    prepared = []  # (product, quantity, unit_price, serials, units_to_sell, details)
    for line in lines:
        product = products.get(str(line.get("product_id", "")))
        if product is None:
            raise BillError("One of the items no longer exists.", {"lines": "One of the items no longer exists."})
        # IMEIs are only kept for a mobiles organization; for others the line is plain quantity.
        serials = (
            [normalize_serial(x) for x in (line.get("serials") or []) if normalize_serial(x)]
            if tracks_phones() else []
        )
        default_price = product.sale_price if kind == "sale" else product.purchase_price
        raw_price = line.get("unit_price")
        unit_price = _money(default_price if raw_price in (None, "") else raw_price, "unit_price")
        units = []
        details = {}
        if kind == "sale":
            if serials:
                _check_seen_twice(serials)
                units = _check_units_for_sale(product, serials)
                quantity = len(serials)
            elif is_tracked(product):
                message = f"Scan the IMEI of each {product.name} you sell. It is tracked phone by phone."
                raise BillError(message, {"lines": message})
            else:
                quantity = _quantity(line.get("quantity", 1))
        elif serials:
            _check_seen_twice(serials)
            _check_new_serials(product, serials)
            details = clean_details(line.get("details"))
            _check_second_imei(details, serials)
            quantity = len(serials)
        else:
            quantity = _quantity(line.get("quantity", 1))
        prepared.append((product, quantity, unit_price, serials, units, details))

    bill_no = _new_bill_no(kind, date)
    total = Decimal("0")
    rows = []
    for product, quantity, unit_price, serials, _units, _details in prepared:
        # One row per phone, so each phone shows on its own in the Day Book.
        pieces = [(1, serial) for serial in serials] if serials else [(quantity, "")]
        for piece_qty, serial in pieces:
            amount = (unit_price * piece_qty).quantize(Decimal("0.01"))
            total += amount
            common = {
                "date": date,
                "product": product,
                "subcategory": subcategory,
                "product_category": product.category,
                "quantity": piece_qty,
                "payment_mode": payment_mode,
                "bank_account": bank_account if payment_mode == PaymentMode.BANK else None,
                "bill_no": bill_no,
                "created_by_email": created_by_email,
                "note": f"IMEI {serial}" if serial else "",
            }
            if kind == "sale":
                rows.append(SalesEntry(
                    channel=category, gross_amount=amount, discount=Decimal("0"), amount=amount, **common,
                ))
            else:
                rows.append(PurchaseEntry(
                    category=category, vendor=vendor[:150], amount=amount, **common,
                ))

    for row in rows:
        row.save()
    # Stock changes happen only after every row is written.
    for product, _quantity_, unit_price, serials, units, details in prepared:
        for unit in units:
            unit.status = StockUnit.Status.SOLD
            unit.sale_bill_no = bill_no
            unit.sale_date = date
            unit.save()
        if kind == "purchase":
            for serial in serials:
                # Brand and model start from the item (its sub-category and name); the line can override.
                fields = {
                    "brand": product.brand or (product.subcategory.name if product.subcategory_id else ""),
                    "model_name": product.name,
                    "colour": product.colour,
                    "ram": product.ram,
                    "storage": product.storage,
                }
                fields.update(details)
                StockUnit.objects.create(
                    product=product,
                    serial=serial,
                    purchase_bill_no=bill_no,
                    purchase_date=date,
                    **fields,
                )
    return {"bill_no": bill_no, "rows": rows, "total": total}


def _check_seen_twice(serials: list) -> None:
    if len(set(serials)) != len(serials):
        message = "The same IMEI is listed twice on this bill."
        raise BillError(message, {"lines": message})


def code_json(kind: str, item) -> dict:
    """The scan result for a code: the item found, plus what kind of item it is."""
    if kind == "product":
        return {"type": "product", "product": product_json(item)}
    if kind == "unit":
        return {
            "type": "unit",
            "unit": {
                "id": str(item.pk),
                "serial": item.serial,
                "status": item.status,
                "product": product_json(item.product),
                "description": unit_description(item),
                "brand": item.brand,
                "model": item.model_name,
                "colour": item.colour,
                "ram": item.ram,
                "storage": item.storage,
                "imei2": item.imei2,
                "other": item.other_details,
            },
        }
    if kind == "category":
        return {"type": "category", "category": {"id": str(item.pk), "name": item.name, "kind": item.kind}}
    return {
        "type": "subcategory",
        "subcategory": {
            "id": str(item.pk),
            "name": item.name,
            "category_id": str(item.category_id),
            "category_name": item.category.name,
            "category_kind": item.category.kind,
        },
    }


def subcategory_path(subcategory: Subcategory | None) -> str:
    """'Mobiles › Vivo' for a sub-category under a category; '' when there is none."""
    if subcategory is None:
        return ""
    return f"{subcategory.category.name} › {subcategory.name}"


def product_json(product: Product) -> dict:
    return {
        "tracked": is_tracked(product),
        "brand": product.brand,
        "size": product.size,
        "colour": product.colour,
        "ram": product.ram,
        "storage": product.storage,
        "extra": product.extra_details,
        "opening_stock": product.opening_stock,
        "attribute_keys": ATTRIBUTES_BY_TYPE.get(inventory_type(), ATTRIBUTES_BY_TYPE["OTHER"]),
        "attributes_text": attributes_text(product),
        "path": product_path(product),
        "common": common_values(product),
        "subcategory_id": str(product.subcategory_id) if product.subcategory_id else "",
        "subcategory_path": subcategory_path(product.subcategory),
        "id": str(product.pk),
        "barcode": product.barcode,
        "name": product.name,
        "category": product.category.name if product.category_id else "",
        "sale_price": f"{product.sale_price:.2f}",
        "purchase_price": f"{product.purchase_price:.2f}",
    }


def pick(queryset, raw):
    """The row with this pk from `queryset`, or None for a blank or malformed id."""
    if not raw:
        return None
    try:
        return queryset.filter(pk=raw).first()
    except (ValueError, ValidationError):
        return None


def barcode_scanning_on(user) -> bool:
    """Whether this user's organization has barcode scanning turned on. It is off
    for every organization until a superadmin enables it."""
    organization = getattr(user, "organization", None)
    return bool(organization and organization.barcode_scanning_enabled)


DISABLED_MESSAGE = "Barcode scanning is turned off for your organization. Ask Finday support to turn it on."


def pick_pk(raw):
    """A UUID from a query string, or None when it isn't one."""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
