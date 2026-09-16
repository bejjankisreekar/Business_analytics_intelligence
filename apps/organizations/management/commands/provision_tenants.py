from django.core.management.base import BaseCommand

from apps.organizations.models import Organization
from apps.organizations.tenant import provision_tenant_schema


class Command(BaseCommand):
    help = "Clone the finance app tables + seed defaults into every organization's tenant schema."

    def handle(self, *args, **options):
        orgs = Organization.objects.all()
        for org in orgs:
            self.stdout.write(f"Provisioning {org.name} ({org.schema_name})...")
            provision_tenant_schema(org)
        self.stdout.write(self.style.SUCCESS(f"Provisioned {orgs.count()} organization(s)."))
