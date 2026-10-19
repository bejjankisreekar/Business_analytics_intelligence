"""Which finance pages a manager account may open.

The organization owner/admin ticks the pages on the Create Manager form (and
can change them later from the profile). The choice is stored on the manager's
User row in `manager_pages` as a list of the keys below.

Enforcement is done in one place — `ManagerPageAccessMiddleware` — by matching
the requested URL name against `URL_NAMES_BY_PAGE`. A finance URL that is not
listed under any page is denied to managers, so a new page is closed by
default until it is registered here.
"""

from django.urls import reverse

# (key, label, finance url names that belong to the page)
MANAGER_PAGES = (
    ("daily_report", "Daily Book", (
        "daily_report", "daily_report_pdf", "daily_entry_days", "daily_bulk_entry",
        "bulk_add_sales", "bulk_add_expenses", "bulk_add_purchases", "single_entry",
        "import_sales", "import_sales_sample", "import_sales_commit",
        "add_transfer", "edit_transfer", "delete_transfer",
        "add_customer", "toggle_customer",
    )),
    ("daily_summary", "Daily Performance", (
        "daily_summary", "daily_summary_day_detail", "daily_summary_excel", "daily_summary_pdf",
    )),
    ("dashboard", "Dashboard", ("dashboard",)),
    ("analytics", "Business Analytics", ("analytics", "subcategory_detail")),
    ("sales_intelligence", "Revenue Insights", (
        "sales_intelligence", "sales_intelligence_details", "sales_channel_trend",
    )),
    ("purchase_expense_intelligence", "Cost Insights", (
        "purchase_expense_intelligence", "expense_category_trend",
    )),
    ("monthly_summary", "Monthly Summary", (
        "monthly_summary", "monthly_summary_excel", "monthly_summary_pdf",
    )),
    ("category_statement", "Category Statement", (
        "category_statement", "category_statement_excel", "category_statement_csv",
        "category_statement_pdf",
    )),
    ("financial_reports", "Financial Reports", (
        "reports", "report_line_detail", "pnl_pdf", "balance_sheet_pdf", "cash_flow_pdf", "gst_pdf",
    )),
    ("cash_position", "Cash Position", (
        "cash_position", "aging_report",
        "add_receivable", "edit_receivable", "pay_receivable", "delete_receivable",
        "add_payable", "edit_payable", "pay_payable", "delete_payable",
        "add_partner", "add_partner_transaction", "edit_partner_transaction", "delete_partner_transaction",
        "partner_ledger", "partner_ledger_excel", "partner_ledger_csv", "partner_ledger_pdf",
    )),
    ("ledgers", "Ledgers", (
        "ledgers", "add_general_ledger", "general_ledger", "delete_general_ledger",
        "add_ledger_entry", "delete_ledger_entry", "cash_ledger", "bank_ledger",
        "customer_ledger", "vendor_ledger", "bank_account_ledger",
    )),
    ("kickbacks", "IP Admissions", (
        "kickbacks", "add_kickback_entry", "kickback_column_labels", "kickback_detail",
        "edit_kickback_entry", "delete_kickback_entry", "kickbacks_csv", "kickbacks_excel",
        "kickbacks_pdf", "import_kickback_entries", "kickbacks_import_sample",
    )),
    ("insurance_claims", "Insurance Claims", (
        "claims", "add_claim", "edit_claim", "claim_detail", "delete_claim",
        "add_claim_payment", "delete_claim_payment", "claims_excel", "claims_pdf", "add_patient_payment",
    )),
    ("setup", "Setup & masters", (
        "settings", "categories", "add_category", "edit_category", "toggle_category", "delete_category",
        "add_subcategory", "edit_subcategory", "toggle_subcategory", "delete_subcategory",
        "vendors", "add_vendor", "edit_vendor", "toggle_vendor", "delete_vendor",
        "bank_accounts", "add_bank_account", "edit_bank_account", "toggle_bank_account",
        "delete_bank_account",
    )),
)

PAGE_CHOICES = [(key, label) for key, label, _ in MANAGER_PAGES]
PAGE_KEYS = frozenset(key for key, _ in PAGE_CHOICES)

# Only the Daily Book (entry screen) is on by default, matching what managers
# could already open before per-page access existed.
DEFAULT_MANAGER_PAGES = ("daily_report",)

# Finance URLs every manager can open regardless of the checkboxes: the
# billing screens, which also host the self-service payment for a lapsed
# subscription (TenantLoginRequiredMixin redirects there).
ALWAYS_ALLOWED_URL_NAMES = frozenset({
    "billing", "invoice_detail", "create_invoice_payment_order", "verify_invoice_payment",
    "apply_coupon", "create_autopay_subscription", "verify_autopay_setup", "cancel_autopay",
})

URL_NAMES_BY_PAGE = {key: frozenset(names) for key, _, names in MANAGER_PAGES}

_PAGE_BY_URL_NAME = {name: key for key, names in URL_NAMES_BY_PAGE.items() for name in names}


# Roles whose finance pages are limited to a list of pages.
RESTRICTED_ROLES = ("MANAGER",)


def pages_for(user) -> tuple:
    """The page keys a restricted user may open: the list their owner ticked."""
    return tuple(getattr(user, "manager_pages", None) or ())


def manager_can_open(manager_pages, url_name: str) -> bool:
    """True if a manager holding `manager_pages` may open the finance URL `url_name`."""
    if url_name in ALWAYS_ALLOWED_URL_NAMES:
        return True
    page = _PAGE_BY_URL_NAME.get(url_name)
    return page is not None and page in manager_pages


def first_allowed_url(manager_pages) -> str:
    """Where to send a restricted user who asked for a page they don't have."""
    if "daily_report" in manager_pages:
        return reverse("finance:daily_report")
    if "kickbacks" in manager_pages:
        return reverse("finance:kickbacks")
    if "insurance_claims" in manager_pages:
        return reverse("finance:claims")
    return reverse("accounts:profile")


class PageAccess:
    """Template helper: `{% if "dashboard" in page_access %}`.

    Non-manager users see every page, so the same sidebar works for everyone.
    """

    def __init__(self, user):
        from .models import User

        self._unrestricted = getattr(user, "role", None) not in RESTRICTED_ROLES
        self._pages = frozenset(pages_for(user))

    def __contains__(self, key) -> bool:
        return self._unrestricted or key in self._pages
