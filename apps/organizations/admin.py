from django.contrib import admin

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "organization_code", "business_type", "is_active", "created_at")
    search_fields = ("name", "organization_code")
    readonly_fields = ("organization_code", "created_at", "updated_at")
