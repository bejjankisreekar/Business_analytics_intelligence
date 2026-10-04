import datetime
import random

from django.core.management.base import BaseCommand, CommandError

from apps.organizations.drive_sync import get_valid_access_token
from apps.organizations.models import Organization
from apps.organizations.tenant_context import set_current_organization
from apps.sheets_store.session import SheetSession, set_active_session


class Command(BaseCommand):
    help = "Seed realistic demo sales/expense/purchase/transfer entries into one organization's live database."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization code or slug, e.g. 37A256C2 or acme-retail-pvt-ltd")
        parser.add_argument("--months", type=int, default=6, help="How many months of history to generate")
        parser.add_argument("--clear", action="store_true", help="Delete existing entries for this org first")

    def handle(self, *args, **options):
        org = (
            Organization.objects.filter(organization_code__iexact=options["org"]).first()
            or Organization.objects.filter(slug=options["org"]).first()
        )
        if org is None:
            raise CommandError(f"No organization matches '{options['org']}'.")

        if org.storage_mode == Organization.StorageMode.OUR_DATABASE:
            set_current_organization(org)
            try:
                self._seed(org, months=options["months"], clear=options["clear"])
            finally:
                set_current_organization(None)
        else:
            connection = getattr(org, "cloud_backup", None)
            if connection is None or not connection.external_file_id:
                raise CommandError(f"{org.name} hasn't connected Google Drive yet — nothing to seed.")

            access_token = get_valid_access_token(connection)
            set_active_session(SheetSession(access_token, connection.external_file_id))
            try:
                self._seed(org, months=options["months"], clear=options["clear"])
            finally:
                set_active_session(None)

        self.stdout.write(self.style.SUCCESS(f"Seeded demo data for {org.name} ({org.organization_code})."))

    def _seed(self, org, *, months: int, clear: bool):
        from apps.finance.models import (
            CashTransfer, Category, ExpenseEntry, FinanceSettings, PaymentMode, PurchaseEntry, SalesEntry,
        )

        if clear:
            for model in (SalesEntry, ExpenseEntry, PurchaseEntry, CashTransfer):
                for instance in list(model.objects.all()):
                    instance.delete()
            self.stdout.write("Cleared existing entries.")

        rng = random.Random(42)

        sales_channels = {c.name: c for c in Category.objects.filter(kind=Category.Kind.SALES)}
        expense_categories = {c.name: c for c in Category.objects.filter(kind=Category.Kind.EXPENSE)}
        purchase_categories = {c.name: c for c in Category.objects.filter(kind=Category.Kind.PURCHASE)}

        fs = FinanceSettings.objects.first()
        today = datetime.date.today()
        start = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        for _ in range(months - 1):
            start = (start - datetime.timedelta(days=1)).replace(day=1)
        if fs and fs.opening_date > start:
            fs.opening_date = start
            fs.opening_bank_balance = fs.opening_bank_balance or 25000
            fs.save(update_fields=["opening_date", "opening_bank_balance"])

        vendors = ["Sunrise Wholesale", "Metro Distributors", "Prime Supplies Co", "Kavya Traders", "Local Supplier Co"]

        # Online sales are settled to the bank; in-store/wholesale/other are cash-in-hand.
        channel_payment_mode = {"Online": PaymentMode.BANK}

        sales_rows, expense_rows, purchase_rows, transfer_rows = [], [], [], []

        day = start
        month_seen = set()
        days_since_deposit = 0
        while day <= today:
            weekday = day.weekday()  # 0=Mon .. 6=Sun
            is_weekend = weekday >= 5
            # slow, gentle month-over-month growth so the trend chart shows improvement
            months_elapsed = (day.year - start.year) * 12 + (day.month - start.month)
            growth = 1 + months_elapsed * 0.045

            # --- Sales: 1-3 entries/day, weekend boosted, small chance of a slow day ---
            num_sales = rng.choice([1, 1, 2, 2, 3]) if not is_weekend else rng.choice([2, 3, 3, 4])
            for _ in range(num_sales):
                base = rng.uniform(2500, 6500) * (1.5 if is_weekend else 1.0) * growth
                if rng.random() < 0.06:  # occasional bad day
                    base *= 0.3
                channel_name = rng.choice(list(sales_channels)) if sales_channels else None
                sales_rows.append(SalesEntry(
                    date=day,
                    channel=sales_channels.get(channel_name),
                    # bulk_create() bypasses SalesEntry.save() (the
                    # gross/discount -> amount derivation), so set both
                    # explicitly here to keep them consistent.
                    gross_amount=round(base, 2),
                    amount=round(base, 2),
                    payment_mode=channel_payment_mode.get(channel_name, PaymentMode.CASH),
                    note="",
                ))

            # --- Recurring monthly expenses, booked on the 1st (or start day of first month), paid from the bank ---
            if day.day == 1 or (day == start and start.day != 1):
                if day.month not in month_seen or day == start:
                    month_seen.add(day.month)
                    recurring = [
                        ("Rent", 18000),
                        ("Salaries & Wages", 42000),
                        ("Utilities", rng.uniform(3500, 5200)),
                        ("Taxes", rng.uniform(2000, 6000)),
                    ]
                    for name, amt in recurring:
                        cat = expense_categories.get(name)
                        if cat:
                            expense_rows.append(ExpenseEntry(
                                date=day, category=cat, amount=round(amt, 2),
                                payment_mode=PaymentMode.BANK, note="",
                            ))

            # --- Ad-hoc smaller expenses, a few times a week, usually paid in cash ---
            if rng.random() < 0.35:
                name = rng.choice(["Marketing", "Logistics & Delivery", "Maintenance", "Bank & Payment Charges", "Miscellaneous"])
                cat = expense_categories.get(name)
                if cat:
                    expense_rows.append(ExpenseEntry(
                        date=day, category=cat, amount=round(rng.uniform(300, 3200), 2),
                        payment_mode=PaymentMode.BANK if name == "Bank & Payment Charges" else PaymentMode.CASH,
                        note="",
                    ))

            # --- Purchases / restocking, every 2-3 days, split cash/bank ---
            if rng.random() < 0.4:
                name = rng.choice(["Inventory / Stock", "Raw Materials", "Packaging", "Equipment"])
                cat = purchase_categories.get(name)
                if cat:
                    purchase_rows.append(PurchaseEntry(
                        date=day, category=cat, vendor=rng.choice(vendors),
                        amount=round(rng.uniform(1500, 9000) * growth, 2),
                        payment_mode=PaymentMode.BANK if rng.random() < 0.4 else PaymentMode.CASH,
                        note="",
                    ))

            # --- Periodic cash deposit to the bank, roughly every ~9 days ---
            days_since_deposit += 1
            if days_since_deposit >= 9 and not is_weekend:
                transfer_rows.append(CashTransfer(
                    date=day, direction=CashTransfer.Direction.CASH_TO_BANK,
                    amount=round(rng.uniform(8000, 20000) * growth, 2),
                    note="Weekly cash deposit",
                ))
                days_since_deposit = 0

            day += datetime.timedelta(days=1)

        SalesEntry.objects.bulk_create(sales_rows)
        ExpenseEntry.objects.bulk_create(expense_rows)
        PurchaseEntry.objects.bulk_create(purchase_rows)
        CashTransfer.objects.bulk_create(transfer_rows)

        self.stdout.write(
            f"  {len(sales_rows)} sales, {len(expense_rows)} expenses, {len(purchase_rows)} purchases, "
            f"{len(transfer_rows)} transfers from {start} to {today}."
        )
