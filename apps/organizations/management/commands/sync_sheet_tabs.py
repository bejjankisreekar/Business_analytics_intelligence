from django.core.management.base import BaseCommand

from apps.organizations.drive_sync import get_valid_access_token
from apps.organizations.models import Organization
from apps.sheets_store.provisioning import add_missing_tabs


class Command(BaseCommand):
    help = (
        "Adds any tab that's missing from a GOOGLE_SHEETS org's spreadsheet "
        "for an apps.finance model added after that org's sheet was first "
        "provisioned (provision_sheet_tenant only creates tabs for the models "
        "that existed at signup time). Safe to run repeatedly — only adds "
        "tabs that don't exist yet."
    )

    def handle(self, *args, **options):
        orgs = Organization.objects.filter(storage_mode=Organization.StorageMode.GOOGLE_SHEETS)
        skipped = 0
        for org in orgs:
            connection = getattr(org, "cloud_backup", None)
            if connection is None or not connection.external_file_id:
                self.stdout.write(f"{org.name} ({org.organization_code}): Google Drive not connected, skipping")
                skipped += 1
                continue

            access_token = get_valid_access_token(connection)
            added = add_missing_tabs(access_token, connection.external_file_id)
            if not added:
                self.stdout.write(f"{org.name} ({org.organization_code}): already up to date")
                continue
            self.stdout.write(f"{org.name} ({org.organization_code}): added tab(s) {', '.join(added)}")
        self.stdout.write(
            self.style.SUCCESS(f"Synced sheet tabs for {orgs.count() - skipped} organization(s), {skipped} skipped.")
        )
