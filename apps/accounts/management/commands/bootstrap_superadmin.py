from decouple import config
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = (
        "Create or update the platform superadmin user from SUPERADMIN_EMAIL / "
        "SUPERADMIN_PASSWORD in .env. Safe to re-run any time those values change."
    )

    def handle(self, *args, **options):
        email = config("SUPERADMIN_EMAIL", default="").strip().lower()
        password = config("SUPERADMIN_PASSWORD", default="")

        if not email or not password:
            raise CommandError(
                "SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD must both be set in .env"
            )

        user, created = User.objects.get_or_create(email=email)
        user.set_password(password)
        user.role = User.Role.SUPER_ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.organization = None
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} superadmin user: {user.email}"))
