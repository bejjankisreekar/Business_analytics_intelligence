"""Creates a brand-new org's live Google Sheet, run once, right when
Google Drive finishes connecting (see
apps.accounts.views.GoogleDriveOAuthCallbackView). Everything from
here on is apps.finance's actual live database — nothing is ever
written to our own disk or database for finance data.
"""
from __future__ import annotations

import datetime

from django.apps import apps as django_apps

from . import client, store
from .session import SheetSession, set_active_session

TENANT_APP_LABEL = "finance"

DEFAULT_EXPENSE_CATEGORIES = [
    "Rent", "Salaries & Wages", "Utilities", "Marketing", "Logistics & Delivery",
    "Maintenance", "Taxes", "Bank & Payment Charges", "Miscellaneous",
]
DEFAULT_PURCHASE_CATEGORIES = ["Raw Materials", "Inventory / Stock", "Packaging", "Equipment", "Other"]
DEFAULT_SALES_CHANNELS = ["In-store", "Online", "Wholesale", "Other"]


def tenant_models() -> list:
    return list(django_apps.get_app_config(TENANT_APP_LABEL).get_models())


def _header_row(model) -> list:
    return [field.verbose_name.title() for field in store.sheet_fields(model)]


def provision_sheet_tenant(org, *, access_token: str, folder_id: str) -> str:
    """Creates the spreadsheet (one tab per finance model, header row
    only), seeds default categories + FinanceSettings as real rows, and
    returns the new spreadsheet id.
    """
    models = tenant_models()
    spreadsheet_id = client.create_spreadsheet(
        access_token,
        title=f"{org.organization_code} Data",
        tab_names=[m.__name__ for m in models],
        folder_id=folder_id,
    )

    header_data = [{"range": f"{m.__name__}!A1", "values": [_header_row(m)]} for m in models]
    client.batch_update_values(access_token, spreadsheet_id=spreadsheet_id, data=header_data)

    session = SheetSession(access_token, spreadsheet_id)
    set_active_session(session)
    try:
        from apps.finance.models import Category, FinanceSettings

        store.create(FinanceSettings, fy_start_month=4, opening_balance=0, opening_date=datetime.date.today())
        for name in DEFAULT_EXPENSE_CATEGORIES:
            store.create(Category, kind=Category.Kind.EXPENSE, name=name)
        for name in DEFAULT_PURCHASE_CATEGORIES:
            store.create(Category, kind=Category.Kind.PURCHASE, name=name)
        for name in DEFAULT_SALES_CHANNELS:
            store.create(Category, kind=Category.Kind.SALES, name=name)
    finally:
        set_active_session(None)

    return spreadsheet_id
