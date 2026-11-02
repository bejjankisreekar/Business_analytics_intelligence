import datetime
import json

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.generic import View

from . import barcode_labels, inventory, scan_billing
from .models import BankAccount, Category, CommonOption, PaymentMode, Product, StockUnit, Subcategory
from .views import TenantLoginRequiredMixin, _historical_min_date


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error(exc: scan_billing.BillError, code: str = "validation"):
    return JsonResponse({"error": exc.message, "code": code, "fields": exc.fields}, status=400)


def _disabled(request):
    """A 403 for an organization without barcode scanning, or None when it is on."""
    if scan_billing.barcode_scanning_on(request.user):
        return None
    return JsonResponse({"error": scan_billing.DISABLED_MESSAGE, "code": "feature_disabled"}, status=403)


class ScanBillView(TenantLoginRequiredMixin, View):
    """The scan-and-bill screen: scan (camera or USB barcode gun) items into a
    bill, then save it as sales or purchase rows."""

    template_name = "finance/scan_bill.html"

    def get(self, request, *args, **kwargs):
        if not scan_billing.barcode_scanning_on(request.user):
            raise Http404("Barcode scanning is not enabled for this organization.")
        kind = "purchase" if request.GET.get("type") == "purchase" else "sale"
        min_date = _historical_min_date(request)
        try:
            day = datetime.date.fromisoformat(request.GET.get("date", ""))
        except ValueError:
            day = datetime.date.today()
        context = {
            "active_nav": "daily_report",
            "organization": request.user.organization,
            "kind": kind,
            "channels": Category.objects.filter(kind=Category.Kind.SALES, is_active=True).order_by("name"),
            "purchase_categories": Category.objects.filter(kind=Category.Kind.PURCHASE, is_active=True).order_by("name"),
            "product_categories": Category.objects.filter(kind=Category.Kind.PRODUCT, is_active=True).order_by("name"),
            "product_subcategories": Subcategory.objects.filter(is_active=True).select_related("category").order_by("category__name", "name"),
            "subcategories": Subcategory.objects.filter(
                is_active=True,
                category__kind=Category.Kind.SALES if kind == "sale" else Category.Kind.PURCHASE,
            ).select_related("category").order_by("category__name", "name"),
            "payment_modes": PaymentMode.choices,
            "bank_accounts": BankAccount.objects.filter(is_active=True).order_by("name"),
            "min_date": min_date.isoformat() if min_date else "",
            "daily_url": reverse("finance:daily_report"),
            "bill_date": day.isoformat(),
            "inventory_type": scan_billing.inventory_type(),
            "open_camera": request.GET.get("camera") == "1",
        }
        urls = {
            "lookupUrl": reverse("finance:scan_bill_lookup"),
            "productUrl": reverse("finance:scan_bill_product"),
            "searchUrl": reverse("finance:scan_bill_product_search"),
            "unitUrl": reverse(
                "finance:scan_bill_unit", args=["00000000-0000-0000-0000-000000000000"],
            ).replace("00000000-0000-0000-0000-000000000000", "__ID__"),
            "productDetailUrl": reverse(
                "finance:scan_bill_product_detail", args=["00000000-0000-0000-0000-000000000000"],
            ).replace("00000000-0000-0000-0000-000000000000", "__ID__"),
            "saveUrl": reverse("finance:scan_bill_save"),
        }
        context["sb_config"] = {
            "kind": kind,
            "currency": request.user.organization.currency,
            "openCamera": context["open_camera"],
            "inventoryType": scan_billing.inventory_type(),
            "structure": scan_billing.structure_config(),
            "attributeKeys": scan_billing.ATTRIBUTES_BY_TYPE.get(
                scan_billing.inventory_type(), scan_billing.ATTRIBUTES_BY_TYPE["OTHER"],
            ),
            **urls,
        }
        return render(request, self.template_name, context)


class ScanBillLookupView(TenantLoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        kind, item = scan_billing.resolve_code(request.GET.get("barcode", ""))
        if kind is None:
            return JsonResponse({"error": "No product, category or sub-category has this barcode.", "code": "not_found"}, status=404)
        return JsonResponse(scan_billing.code_json(kind, item))


class ScanBillProductView(TenantLoginRequiredMixin, View):
    """Adds a product from the scan screen, when a barcode is not known yet."""

    def post(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        data = _body(request)
        category = scan_billing.pick(Category.objects.filter(kind=Category.Kind.PRODUCT), data.get("category"))
        subcategory = scan_billing.pick(Subcategory.objects.filter(is_active=True), data.get("subcategory"))
        try:
            product = scan_billing.create_product(
                barcode=data.get("barcode", ""),
                name=data.get("name", ""),
                sale_price=data.get("sale_price"),
                purchase_price=data.get("purchase_price"),
                category=category,
                subcategory=subcategory,
                attributes=scan_billing.clean_attributes(data),
                opening_stock=data.get("opening_stock"),
                path=data["path"] if isinstance(data.get("path"), list) else None,
                common=data.get("common"),
            )
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({"product": scan_billing.product_json(product)}, status=201)


class ScanBillUnitView(TenantLoginRequiredMixin, View):
    """Edits what is recorded for one phone (brand, model, colour, RAM, storage, second IMEI, notes)."""

    def post(self, request, pk, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        unit = StockUnit.objects.filter(pk=pk).first()
        if unit is None:
            return JsonResponse({"error": "That phone was not found.", "code": "not_found"}, status=404)
        try:
            unit = scan_billing.update_unit_details(unit, _body(request))
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({"unit": scan_billing.code_json("unit", unit)["unit"]})


class ScanBillUnitAddView(TenantLoginRequiredMixin, View):
    """Adds a phone that was already in stock, by its IMEI, with no purchase bill (used from Setup)."""

    def post(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        data = _body(request)
        product = scan_billing.pick(Product.objects.filter(is_active=True), data.get("product"))
        if product is None:
            message = "Pick the item this phone is."
            return JsonResponse({"error": message, "code": "validation", "fields": {"product": message}}, status=400)
        try:
            unit = scan_billing.add_opening_unit(product, data.get("serial"), data)
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({"unit": scan_billing.code_json("unit", unit)["unit"]}, status=201)


class CommonOptionAddView(TenantLoginRequiredMixin, View):
    """Adds a value to a common list (Colour, RAM ...) that every item can pick from."""

    def post(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        data = _body(request)
        try:
            option = scan_billing.add_common_option(data.get("level"), data.get("value"))
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({"option": {"id": str(option.pk), "level": option.level, "value": option.value}}, status=201)


class CommonOptionRemoveView(TenantLoginRequiredMixin, View):
    """Takes a value out of a common list. Items that already have it keep it."""

    def post(self, request, pk, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        option = CommonOption.objects.filter(pk=pk).first()
        if option is None:
            return JsonResponse({"error": "That value was not found.", "code": "not_found"}, status=404)
        option.delete()
        return JsonResponse({"removed": str(pk)})


class ScanBillProductSearchView(TenantLoginRequiredMixin, View):
    """Products whose name contains the text typed, for adding an item without scanning it."""

    def get(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        query = request.GET.get("q", "").strip().lower()
        if len(query) < 2:
            return JsonResponse({"products": []})
        # Matched in Python, so the same code works for Google Sheets and database storage.
        matches = [
            scan_billing.product_json(p)
            for p in Product.objects.filter(is_active=True).order_by("name")
            if query in p.name.lower()
        ][:15]
        return JsonResponse({"products": matches})


class ScanBillProductDetailView(TenantLoginRequiredMixin, View):
    """Edits a saved product's name, code and prices from the scan screen."""

    def post(self, request, pk, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        product = Product.objects.filter(pk=pk, is_active=True).first()
        if product is None:
            return JsonResponse({"error": "That product was not found.", "code": "not_found"}, status=404)
        data = _body(request)
        # Keep the current category unless the screen sends a new one.
        category = (
            scan_billing.pick(Category.objects.filter(kind=Category.Kind.PRODUCT), data.get("category"))
            if "category" in data else product.category
        )
        keep_subcategory = "subcategory" not in data
        subcategory = scan_billing.pick(Subcategory.objects.filter(is_active=True), data.get("subcategory"))
        try:
            product = scan_billing.update_product(
                product,
                name=data.get("name", ""),
                barcode=data.get("barcode", ""),
                sale_price=data.get("sale_price"),
                purchase_price=data.get("purchase_price"),
                category=category,
                subcategory=subcategory,
                keep_subcategory=keep_subcategory,
                attributes=scan_billing.clean_attributes(data, keep_blank=True),
                opening_stock=data.get("opening_stock") if "opening_stock" in data else None,
                path=data["path"] if isinstance(data.get("path"), list) else None,
                common=data.get("common"),
            )
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({"product": scan_billing.product_json(product)})


class ScanBillSaveView(TenantLoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if (blocked := _disabled(request)) is not None:
            return blocked
        data = _body(request)
        kind = data.get("type", "sale")
        category_kind = Category.Kind.SALES if kind == "sale" else Category.Kind.PURCHASE
        category = scan_billing.pick(Category.objects.filter(kind=category_kind), data.get("category"))
        bank_account = scan_billing.pick(BankAccount.objects.filter(is_active=True), data.get("bank_account"))
        subcategory = scan_billing.pick(Subcategory.objects.filter(is_active=True), data.get("subcategory"))
        try:
            day = datetime.date.fromisoformat(str(data.get("date") or ""))
        except ValueError:
            message = "Pick a valid date."
            return JsonResponse({"error": message, "code": "validation", "fields": {"date": message}}, status=400)
        try:
            result = scan_billing.save_bill(
                kind=kind,
                date=day,
                payment_mode=data.get("payment_mode", PaymentMode.CASH),
                bank_account=bank_account,
                category=category,
                vendor=str(data.get("vendor") or "").strip(),
                lines=data.get("lines") or [],
                min_date=_historical_min_date(request),
                created_by_email=request.user.email,
                subcategory=subcategory,
            )
        except scan_billing.BillError as exc:
            return _error(exc)
        return JsonResponse({
            "bill_no": result["bill_no"],
            "items": len(result["rows"]),
            "total": f"{result['total']:.2f}",
            "daily_url": f"{reverse('finance:daily_report')}?date={day.isoformat()}",
        }, status=201)


# Every category gets a label, including expense categories and their sub-categories.
SCANNABLE_CATEGORY_KINDS = tuple(Category.Kind.values)


def one_label(kind: str, pk) -> tuple[str, str] | None:
    """(title, code) for one item, giving it a code first if it has none. None when the item isn't found."""
    if kind == "category":
        item = Category.objects.filter(pk=pk, is_active=True).first()
        prefix = scan_billing.CATEGORY_PREFIX
        title = item.name if item else ""
    elif kind == "subcategory":
        item = Subcategory.objects.filter(pk=pk, is_active=True).select_related("category").first()
        prefix = scan_billing.SUBCATEGORY_PREFIX
        title = f"{item.category.name} › {item.name}" if item else ""
    else:
        item = Product.objects.filter(pk=pk, is_active=True).first()
        prefix = scan_billing.PRODUCT_PREFIX
        title = item.name if item else ""
    if item is None:
        return None
    if not item.barcode:
        item.barcode = scan_billing.new_code(prefix)
        item.save()
    return title, item.barcode


def label_items(kind: str) -> list[tuple[str, str]]:
    """(title, code) for every active item of one kind, giving any item without a code a new one first."""
    if kind == "product":
        scan_billing.ensure_codes(Product, scan_billing.PRODUCT_PREFIX)
        return [
            (p.name, p.barcode)
            for p in Product.objects.filter(is_active=True).order_by("name")
        ]
    if kind == "category":
        scan_billing.ensure_codes(Category, scan_billing.CATEGORY_PREFIX)
        return [
            (c.name, c.barcode)
            for c in Category.objects.filter(is_active=True, kind__in=SCANNABLE_CATEGORY_KINDS).order_by("name")
        ]
    scan_billing.ensure_codes(Subcategory, scan_billing.SUBCATEGORY_PREFIX)
    return [
        (f"{s.category.name} › {s.name}", s.barcode)
        for s in Subcategory.objects.filter(is_active=True, category__kind__in=SCANNABLE_CATEGORY_KINDS)
        .select_related("category")
        .order_by("category__name", "name")
    ]


class BarcodeLabelsPageView(TenantLoginRequiredMixin, View):
    """Choose which labels to print: products, categories, sub-categories, or all."""

    template_name = "finance/barcode_labels.html"

    @staticmethod
    def _category_rows():
        # Give every category its code now, so the page shows it next to the print button.
        scan_billing.ensure_codes(Category, scan_billing.CATEGORY_PREFIX)
        return Category.objects.filter(is_active=True, kind__in=SCANNABLE_CATEGORY_KINDS).order_by("kind", "name")

    @staticmethod
    def _subcategory_rows():
        scan_billing.ensure_codes(Subcategory, scan_billing.SUBCATEGORY_PREFIX)
        return (
            Subcategory.objects.filter(is_active=True, category__kind__in=SCANNABLE_CATEGORY_KINDS)
            .select_related("category")
            .order_by("category__name", "name")
        )

    def get(self, request, *args, **kwargs):
        if not scan_billing.barcode_scanning_on(request.user):
            raise Http404("Barcode scanning is not enabled for this organization.")
        context = {
            "active_nav": "daily_report",
            "organization": request.user.organization,
            "product_count": Product.objects.filter(is_active=True).count(),
            "category_count": Category.objects.filter(is_active=True, kind__in=SCANNABLE_CATEGORY_KINDS).count(),
            "subcategory_count": Subcategory.objects.filter(
                is_active=True, category__kind__in=SCANNABLE_CATEGORY_KINDS,
            ).count(),
            "pdf_url": reverse("finance:barcode_labels_pdf"),
            "scan_url": reverse("finance:scan_bill"),
            "categories": self._category_rows(),
            "subcategories": self._subcategory_rows(),
        }
        return render(request, self.template_name, context)


LABELS_PER_SHEET = barcode_labels.COLUMNS * barcode_labels.ROWS
MAX_COPIES = LABELS_PER_SHEET * 10


def _copies(request) -> int:
    """How many identical labels to print for one item: a full sheet by default, at most ten sheets."""
    try:
        copies = int(request.GET.get("copies", LABELS_PER_SHEET))
    except ValueError:
        copies = LABELS_PER_SHEET
    return max(1, min(copies, MAX_COPIES))


class BarcodeLabelsPdfView(TenantLoginRequiredMixin, View):
    """A printable A4 sheet of labels for the chosen kind of item."""

    def get(self, request, *args, **kwargs):
        if not scan_billing.barcode_scanning_on(request.user):
            raise Http404("Barcode scanning is not enabled for this organization.")
        kind = request.GET.get("kind", "product")
        if kind not in ("product", "category", "subcategory"):
            return JsonResponse({"error": "kind must be product, category or subcategory."}, status=400)
        if request.GET.get("id"):
            # One label for one category or sub-category.
            label = one_label(kind, scan_billing.pick_pk(request.GET["id"]))
            if label is None:
                return JsonResponse({"error": "That item was not found."}, status=404)
            # The same code fills the whole sheet, so one print covers every product it goes on.
            labels = [label] * _copies(request)
            filename = f"barcode-label-{kind}-{request.GET['id']}.pdf"
        else:
            labels = label_items(kind)
            filename = f"barcode-labels-{kind}.pdf"
        if not labels:
            return JsonResponse({"error": "There are no items of this kind to print yet."}, status=404)
        response = HttpResponse(barcode_labels.labels_pdf(labels), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


class InventoryView(TenantLoginRequiredMixin, View):
    """Inventory and stock: what was bought, what was sold and what is left for each item,
    the phones tracked by IMEI, and the latest purchases and sales."""

    template_name = "finance/inventory.html"

    def get(self, request, *args, **kwargs):
        if not scan_billing.barcode_scanning_on(request.user):
            raise Http404("Barcode scanning is not enabled for this organization.")
        tab = request.GET.get("tab")
        tab = tab if tab in ("phones", "activity") else "items"
        status = request.GET.get("status", "")
        query = request.GET.get("q", "").strip()
        rows = inventory.inventory_rows()
        context = {
            "active_nav": "daily_report",
            "organization": request.user.organization,
            "tab": tab,
            "inventory_type": scan_billing.inventory_type(),
            "rows": rows,
            "totals": inventory.inventory_totals(rows),
            "units": inventory.unit_rows(status, query) if tab == "phones" else [],
            "movements": inventory.recent_movements() if tab == "activity" else [],
            "status": status,
            "q": query,
            "scan_url": reverse("finance:scan_bill"),
            "inventory_url": reverse("finance:inventory"),
        }
        return render(request, self.template_name, context)
