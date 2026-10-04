from django.core.management.base import BaseCommand

from apps.organizations.drive_sync import get_valid_access_token
from apps.organizations.models import Organization
from apps.sheets_store.session import SheetSession, set_active_session


class Command(BaseCommand):
    help = (
        "Backfill the Vendor directory (Settings) from vendor names already used "
        "on Purchase entries and Payables, for every organization that has connected "
        "Google Drive. The Vendor directory was added after those free-text vendor "
        "fields, so existing orgs have vendor names in their data that never got a "
        "directory entry. Safe to run repeatedly — only creates vendors that don't "
        "exist yet."
    )

    def handle(self, *args, **options):
        from apps.finance.models import Payable, PurchaseEntry, Vendor

        orgs = Organization.objects.all()
        skipped = 0
        for org in orgs:
            connection = getattr(org, "cloud_backup", None)
            if connection is None or not connection.external_file_id:
                self.stdout.write(f"{org.name} ({org.organization_code}): Google Drive not connected, skipping")
                skipped += 1
                continue

            access_token = get_valid_access_token(connection)
            set_active_session(SheetSession(access_token, connection.external_file_id))
            try:
                names = set(PurchaseEntry.objects.exclude(vendor="").values_list("vendor", flat=True)) | set(
                    Payable.objects.exclude(vendor="").values_list("vendor", flat=True)
                )
                existing = set(Vendor.objects.values_list("name", flat=True))
                new_names = sorted(names - existing)
                for name in new_names:
                    Vendor.objects.create(name=name)
            finally:
                set_active_session(None)

            if new_names:
                self.stdout.write(
                    f"{org.name} ({org.organization_code}): added {len(new_names)} vendor(s) — {', '.join(new_names)}"
                )
            else:
                self.stdout.write(f"{org.name} ({org.organization_code}): already up to date")
        self.stdout.write(
            self.style.SUCCESS(f"Backfilled vendors for {orgs.count() - skipped} organization(s), {skipped} skipped.")
        )
