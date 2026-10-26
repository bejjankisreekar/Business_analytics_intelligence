"""Superadmin console's own records, stored in each environment's database
alongside the clients they're about (so a prod action is logged in prod).

- AuditLogEntry: who did what in the console, written automatically for
  every successful change (see apps.superadmin.audit.AuditLogMiddleware).
- ClientNote / ClientTag: internal CRM notes and labels on a client —
  never shown to the client.
"""
from django.db import models

from apps.organizations.models import Organization


class AuditLogEntry(models.Model):
    """Never edited or deleted — the permanent record of console changes."""

    actor_email = models.EmailField()
    action = models.CharField(max_length=64, help_text="URL name of the console action, e.g. invoice_delete")
    summary = models.CharField(max_length=255, blank=True)
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    organization_name = models.CharField(max_length=200, blank=True, help_text="Kept if the client is deleted")
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    path = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["action"]),
            models.Index(fields=["actor_email"]),
        ]

    def __str__(self) -> str:
        return f"{self.actor_email} {self.action} {self.created_at:%Y-%m-%d %H:%M}"


class ClientNote(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="admin_notes")
    author_email = models.EmailField()
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]


class ClientTag(models.Model):
    class Color(models.TextChoices):
        AMBER = "amber", "Amber"
        MINT = "mint", "Mint"
        RED = "red", "Red"
        BLUE = "blue", "Blue"
        VIOLET = "violet", "Violet"
        SLATE = "slate", "Slate"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="admin_tags")
    name = models.CharField(max_length=32)
    color = models.CharField(max_length=10, choices=Color.choices, default=Color.SLATE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="superadmin_unique_tag_per_org"),
        ]

    def __str__(self) -> str:
        return self.name
