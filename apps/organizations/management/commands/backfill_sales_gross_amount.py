from django.core.management.base import BaseCommand
from django.db.models import F

from apps.organizations.models import Organization
from apps.organizations.utils import schema_context


class Command(BaseCommand):
    help = (
        "Backfill SalesEntry.gross_amount for every organization, for entries "
        "logged before the Gross/Discount/Net split existed. Sets "
        "gross_amount = amount (discount stays 0) for any row still at the "
        "column's default of 0. Safe to run repeatedly."
    )

    def handle(self, *args, **options):
        from apps.finance.models import SalesEntry

        orgs = Organization.objects.all()
        for org in orgs:
            with schema_context(org.schema_name):
                updated = SalesEntry.objects.filter(gross_amount=0).exclude(amount=0).update(
                    gross_amount=F("amount")
                )
            if updated:
                self.stdout.write(f"{org.name} ({org.schema_name}): backfilled {updated} row(s)")
            else:
                self.stdout.write(f"{org.name} ({org.schema_name}): already up to date")
        self.stdout.write(self.style.SUCCESS(f"Backfilled gross_amount for {orgs.count()} organization(s)."))
