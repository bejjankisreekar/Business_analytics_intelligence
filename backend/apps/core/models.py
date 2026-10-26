from django.db import models


class ContactMessage(models.Model):
    """A submission from the landing page's Contact form. Lives in the
    `public` schema (like Organization/User) since it's collected before
    anyone is signed into an organization's tenant schema."""

    name = models.CharField(max_length=150)
    organization_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} — {self.organization_name}"
