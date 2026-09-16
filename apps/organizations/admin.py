from django.contrib import admin

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "organization_code", "schema_name", "business_type", "is_active", "created_at")
    search_fields = ("name", "organization_code", "schema_name")
    readonly_fields = ("organization_code", "schema_name", "created_at", "updated_at")
