from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "organization_name", "phone", "created_at")
    search_fields = ("name", "organization_name", "phone", "message")
    readonly_fields = ("created_at",)
