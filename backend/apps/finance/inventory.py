"""Inventory and stock: what was bought, what was sold, and what is left, per item.

Everything is read from the bill rows the Scan & Bill screen writes (each row has a
product and a quantity) and from the phones tracked by IMEI. Nothing here changes data.
"""
import datetime
from decimal import Decimal

from .models import Product, PurchaseEntry, SalesEntry, StockUnit
from .scan_billing import attributes_text, subcategory_path, unit_description


def _totals(model) -> dict:
    """{product id: [quantity, amount]} summed over every bill row that has a product."""
    totals: dict = {}
    for entry in model.objects.filter(product__isnull=False):
        row = totals.setdefault(str(entry.product_id), [0, Decimal("0")])
        row[0] += entry.quantity or 0
        row[1] += entry.amount or Decimal("0")
    return totals


def _unit_counts() -> dict:
    """{product id: {"IN_STOCK": n, "SOLD": n}} for the phones tracked by IMEI."""
    counts: dict = {}
    for unit in StockUnit.objects.all():
        row = counts.setdefault(str(unit.product_id), {"IN_STOCK": 0, "SOLD": 0})
        row[unit.status] = row.get(unit.status, 0) + 1
    return counts


def inventory_rows() -> list[dict]:
    """One row per active item. A phone model counts its in-stock phones by IMEI;
    any other item is bought minus sold."""
    bought = _totals(PurchaseEntry)
    sold = _totals(SalesEntry)
    units = _unit_counts()
    rows = []
    for product in Product.objects.filter(is_active=True).order_by("name"):
        key = str(product.pk)
        purchased_qty, purchase_value = bought.get(key, [0, Decimal("0")])
        sold_qty, sale_value = sold.get(key, [0, Decimal("0")])
        tracked = key in units
        in_stock = units[key]["IN_STOCK"] if tracked else product.opening_stock + purchased_qty - sold_qty
        rows.append({
            "id": key,
            "name": product.name,
            "barcode": product.barcode,
            "path": subcategory_path(product.subcategory),
            "attributes": attributes_text(product),
            "opening": product.opening_stock,
            "tracked": tracked,
            "purchased": purchased_qty,
            "sold": sold_qty,
            "in_stock": in_stock,
            "purchase_value": purchase_value,
            "sale_value": sale_value,
            "stock_value": (product.purchase_price * max(in_stock, 0)).quantize(Decimal("0.01")),
        })
    return rows


def inventory_totals(rows: list[dict]) -> dict:
    return {
        "items": len(rows),
        "purchased": sum(r["purchased"] for r in rows),
        "sold": sum(r["sold"] for r in rows),
        "in_stock": sum(max(r["in_stock"], 0) for r in rows),
        "stock_value": sum((r["stock_value"] for r in rows), Decimal("0")),
        "purchase_value": sum((r["purchase_value"] for r in rows), Decimal("0")),
        "sale_value": sum((r["sale_value"] for r in rows), Decimal("0")),
    }


def recent_movements(days: int = 90, limit: int = 100) -> list[dict]:
    """Purchases and sales of items in the last `days` days, newest first."""
    since = datetime.date.today() - datetime.timedelta(days=days)
    names = {str(p.pk): p.name for p in Product.objects.all()}
    movements = []
    for kind, model in (("purchase", PurchaseEntry), ("sale", SalesEntry)):
        for entry in model.objects.filter(product__isnull=False, date__gte=since):
            movements.append({
                "kind": kind,
                "date": entry.date,
                "product": names.get(str(entry.product_id), ""),
                "quantity": entry.quantity or 0,
                "amount": entry.amount,
                "bill_no": entry.bill_no,
                "note": entry.note,
                "created_at": entry.created_at,
            })
    movements.sort(key=lambda m: (m["date"], m["created_at"] or datetime.datetime.min), reverse=True)
    return movements[:limit]


def unit_rows(status: str = "", query: str = "") -> list[dict]:
    """The phones recorded by IMEI, newest purchase first. `status` is IN_STOCK or SOLD ('' for all);
    `query` matches the IMEI or the model name."""
    query = query.strip().lower()
    names = {str(p.pk): p.name for p in Product.objects.all()}
    rows = []
    for unit in StockUnit.objects.all():
        if status in ("IN_STOCK", "SOLD") and unit.status != status:
            continue
        product = names.get(str(unit.product_id), "")
        haystack = " ".join([
            unit.serial, unit.imei2, product, unit.brand, unit.model_name, unit.colour, unit.ram, unit.storage,
        ]).lower()
        if query and query not in haystack:
            continue
        rows.append({
            "id": str(unit.pk),
            "serial": unit.serial,
            "status": unit.status,
            "product": product,
            "purchase_bill_no": unit.purchase_bill_no,
            "purchase_date": unit.purchase_date,
            "sale_bill_no": unit.sale_bill_no,
            "sale_date": unit.sale_date,
            "description": unit_description(unit),
            "brand": unit.brand,
            "model": unit.model_name,
            "colour": unit.colour,
            "ram": unit.ram,
            "storage": unit.storage,
            "imei2": unit.imei2,
            "other": unit.other_details,
        })
    rows.sort(key=lambda r: r["purchase_date"] or datetime.date.min, reverse=True)
    return rows
