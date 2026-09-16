from django.contrib import admin

from .models import Invoice, Payment, PaymentWebhookEvent, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "monthly_price", "yearly_price", "trial_days", "user_limit", "business_limit")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "subscription_id", "organization", "plan", "status", "payment_status",
        "billing_cycle", "is_current", "is_complimentary", "start_date", "end_date",
    )
    list_filter = ("status", "payment_status", "billing_cycle", "is_current", "is_complimentary")
    search_fields = ("subscription_id", "organization__name", "organization__organization_code")
    autocomplete_fields = ("organization", "plan")
    readonly_fields = ("subscription_id", "final_amount", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Subscriptions are a history trail — never deletable, even by staff.
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "organization", "status", "invoice_date", "due_date", "total", "amount_paid", "amount_due")
    list_filter = ("status", "currency")
    search_fields = ("invoice_number", "organization__name", "organization__organization_code")
    autocomplete_fields = ("organization", "subscription")
    readonly_fields = ("invoice_number", "total", "amount_due", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "payment_id", "organization", "amount", "currency", "gateway", "payment_method",
        "status", "payment_date", "transaction_id",
    )
    list_filter = ("status", "gateway", "payment_method")
    search_fields = ("payment_id", "transaction_id", "organization__name", "organization__organization_code")
    autocomplete_fields = ("organization", "subscription", "invoice")
    readonly_fields = ("payment_id", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("gateway", "event_id", "event_type", "status", "payment", "received_at", "processed_at")
    list_filter = ("gateway", "status")
    search_fields = ("event_id", "event_type")
    readonly_fields = ("gateway", "event_id", "payload", "received_at")

    def has_delete_permission(self, request, obj=None):
        return False
