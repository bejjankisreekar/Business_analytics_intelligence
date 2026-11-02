from django.urls import path

from . import views

app_name = "mobile_api"

urlpatterns = [
    path("login/", views.login, name="login"),
    path("me/", views.me, name="me"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("daily-performance/", views.daily_performance, name="daily_performance"),
    path("intelligence/", views.intelligence, name="intelligence"),
    path("monthly-summary/", views.monthly_summary, name="monthly_summary"),
    path("cash-position/", views.cash_position, name="cash_position"),
    path("entry-options/", views.entry_options, name="entry_options"),
    path("entries/", views.entries, name="entries"),
    path("products/lookup/", views.product_lookup, name="product_lookup"),
    path("products/", views.products, name="products"),
    path("products/search/", views.product_search, name="product_search"),
    path("products/<uuid:pk>/", views.product_edit, name="product_edit"),
    path("stock/", views.stock_units, name="stock_units"),
    path("inventory/", views.inventory_items, name="inventory_items"),
    path("bills/", views.bills, name="bills"),
    path("labels/pdf/", views.barcode_labels_pdf, name="barcode_labels_pdf"),
    path("financial-reports/", views.financial_reports, name="financial_reports"),
    path("bank-accounts/", views.bank_accounts, name="bank_accounts"),
    path("vendors/", views.vendors, name="vendors"),
    path("aging/", views.aging, name="aging"),
    path("day-detail/", views.day_detail, name="day_detail"),
    path("category-detail/", views.category_detail, name="category_detail"),
    path("ledger/", views.ledger, name="ledger"),
    path("billing/", views.billing, name="billing"),
    path("billing/invoices/<int:pk>/pay/order/", views.billing_pay_order, name="billing_pay_order"),
    path("billing/invoices/<int:pk>/pay/verify/", views.billing_pay_verify, name="billing_pay_verify"),
]
