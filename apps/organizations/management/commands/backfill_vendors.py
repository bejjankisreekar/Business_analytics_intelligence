from django.core.management.base import BaseCommand

from apps.organizations.models import Organization
from apps.organizations.utils import schema_context


class Command(BaseCommand):
    help = (
        "Backfill the Vendor directory (Settings) from vendor names already used "
        "on Purchase entries and Payables, for every organization. The Vendor "
        "directory was added after those free-text vendor fields, so existing "
        "orgs have vendor names in their data that never got a directory entry. "
        "Safe to run repeatedly — only creates vendors that don't exist yet."
    )

    def handle(self, *args, **options):
        from apps.finance.models import Payable, PurchaseEntry, Vendor

        orgs = Organization.objects.all()
        for org in orgs:
            with schema_context(org.schema_name):
                names = set(PurchaseEntry.objects.exclude(vendor="").values_list("vendor", flat=True)) | set(
                    Payable.objects.exclude(vendor="").values_list("vendor", flat=True)
                )
                existing = set(Vendor.objects.values_list("name", flat=True))
                new_names = sorted(names - existing)
                for name in new_names:
                    Vendor.objects.create(name=name)
            if new_names:
                self.stdout.write(
                    f"{org.name} ({org.schema_name}): added {len(new_names)} vendor(s) — {', '.join(new_names)}"
                )
            else:
                self.stdout.write(f"{org.name} ({org.schema_name}): already up to date")
        self.stdout.write(self.style.SUCCESS(f"Backfilled vendors for {orgs.count()} organization(s)."))
