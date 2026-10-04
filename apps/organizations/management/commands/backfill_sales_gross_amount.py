from django.core.management.base import BaseCommand

from apps.organizations.drive_sync import get_valid_access_token
from apps.organizations.models import Organization
from apps.sheets_store.session import SheetSession, set_active_session


class Command(BaseCommand):
    help = (
        "Backfill SalesEntry.gross_amount for every organization that has connected "
        "Google Drive, for entries logged before the Gross/Discount/Net split existed. "
        "Sets gross_amount = amount (discount stays 0) for any row still at the "
        "column's default of 0. Safe to run repeatedly."
    )

    def handle(self, *args, **options):
        from apps.finance.models import SalesEntry

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
                stale = [e for e in SalesEntry.objects.filter(gross_amount=0) if e.amount != 0]
                for entry in stale:
                    entry.gross_amount = entry.amount
                    entry.save()
            finally:
                set_active_session(None)

            if stale:
                self.stdout.write(f"{org.name} ({org.organization_code}): backfilled {len(stale)} row(s)")
            else:
                self.stdout.write(f"{org.name} ({org.organization_code}): already up to date")
        self.stdout.write(
            self.style.SUCCESS(f"Backfilled gross_amount for {orgs.count() - skipped} organization(s), {skipped} skipped.")
        )
