from django.core.management.base import BaseCommand

from apps.organizations.models import Organization
from apps.organizations.tenant import sync_tenant_schema


class Command(BaseCommand):
    help = (
        "Bring every organization's tenant schema up to date with the current "
        "apps.finance models — creates any new table and adds any new column "
        "that a model change introduced since the org was provisioned."
    )

    def handle(self, *args, **options):
        orgs = Organization.objects.all()
        for org in orgs:
            changes = sync_tenant_schema(org.schema_name)
            if changes:
                self.stdout.write(f"{org.name} ({org.schema_name}): " + ", ".join(changes))
            else:
                self.stdout.write(f"{org.name} ({org.schema_name}): already up to date")
        self.stdout.write(self.style.SUCCESS(f"Synced {orgs.count()} organization(s)."))
