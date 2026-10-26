from apps.organizations.models import Organization
from apps.organizations.services import OrganizationSignupError, delete_organization_and_tenant
from apps.organizations.testing import SheetsBackedTestCase


class DuplicateSignupRaceTests(SheetsBackedTestCase):
    """The signup form already rejects a duplicate email/username before
    hitting the DB, but that check and the actual save aren't atomic — two
    near-simultaneous signups can both pass form validation and only the
    DB's unique constraint catches the second one. That should surface as a
    friendly OrganizationSignupError, not a raw IntegrityError/500."""

    def setUp(self):
        super().setUp()
        self.org, self.owner = self.create_connected_organization(
            org_data={"name": "Race Test Org One", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
            admin_data={"email": "racer@duplicatetest.example", "username": "racer", "password": "ownerpass123"},
        )

    def tearDown(self):
        delete_organization_and_tenant(self.org)

    def test_duplicate_email_raises_signup_error_not_integrity_error(self):
        with self.assertRaises(OrganizationSignupError):
            self.create_connected_organization(
                org_data={"name": "Race Test Org Two", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
                admin_data={"email": "racer@duplicatetest.example", "username": "someoneelse", "password": "ownerpass123"},
            )
        # The whole attempt rolled back — no half-created "Race Test Org Two".
        self.assertFalse(Organization.objects.filter(name="Race Test Org Two").exists())

    def test_duplicate_username_raises_signup_error_not_integrity_error(self):
        with self.assertRaises(OrganizationSignupError):
            self.create_connected_organization(
                org_data={"name": "Race Test Org Three", "business_type": Organization.BusinessType.RETAIL_ECOMMERCE},
                admin_data={"email": "someoneelse@duplicatetest.example", "username": "racer", "password": "ownerpass123"},
            )
        self.assertFalse(Organization.objects.filter(name="Race Test Org Three").exists())
