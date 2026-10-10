from django.core.management.base import BaseCommand

from apps.organizations.drive_sync import get_valid_access_token
from apps.organizations.models import Organization
from apps.sheets_store import client


class Command(BaseCommand):
    help = (
        "Adds any tab that's missing from a GOOGLE_SHEETS org's spreadsheet "
        "for an apps.finance model added after that org's sheet was first "
        "provisioned (provision_sheet_tenant only creates tabs for the models "
        "that existed at signup time). Safe to run repeatedly — only adds "
        "tabs that don't exist yet."
    )

    def handle(self, *args, **options):
        from apps.sheets_store.provisioning import _header_row, tenant_models

        models = tenant_models()
        orgs = Organization.objects.filter(storage_mode=Organization.StorageMode.GOOGLE_SHEETS)
        skipped = 0
        for org in orgs:
            connection = getattr(org, "cloud_backup", None)
            if connection is None or not connection.external_file_id:
                self.stdout.write(f"{org.name} ({org.organization_code}): Google Drive not connected, skipping")
                skipped += 1
                continue

            access_token = get_valid_access_token(connection)
            spreadsheet_id = connection.external_file_id
            existing_tabs = set(client.get_sheet_ids(access_token, spreadsheet_id=spreadsheet_id))
            missing = [m for m in models if m.__name__ not in existing_tabs]
            if not missing:
                self.stdout.write(f"{org.name} ({org.organization_code}): already up to date")
                continue

            client.add_sheet_tabs(access_token, spreadsheet_id=spreadsheet_id, titles=[m.__name__ for m in missing])
            header_data = [{"range": f"{m.__name__}!A1", "values": [_header_row(m)]} for m in missing]
            client.batch_update_values(access_token, spreadsheet_id=spreadsheet_id, data=header_data)
            self.stdout.write(
                f"{org.name} ({org.organization_code}): added tab(s) {', '.join(m.__name__ for m in missing)}"
            )
        self.stdout.write(
            self.style.SUCCESS(f"Synced sheet tabs for {orgs.count() - skipped} organization(s), {skipped} skipped.")
        )
