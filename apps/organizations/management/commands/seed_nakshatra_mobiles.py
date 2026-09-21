"""Seed "Nakshatra Mobiles" — a mobile phone retail shop in Hyderabad — with a
rolling year of realistic trading data: new and pre-owned phones, accessories,
pouches/covers, screen guards, repairs and recharges, plus the matching stock
purchases, shop expenses, payroll, tax, owner drawings and cash/bank movement.

Every product sale carries a unit count (`quantity`). Stock purchases are
derived from what was actually sold, so gross margins look like a real shop's.
"""
import datetime
import math
import random
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.billing import services as billing_services
from apps.billing.models import Plan
from apps.organizations.models import Organization
from apps.organizations.services import (
    create_organization_with_tenant_schema_and_admin,
    delete_organization_and_tenant,
)
from apps.organizations.utils import schema_context

ORG_NAME = "Nakshatra Mobiles"
OWNER_EMAIL = "bharath@nakshatramobiles.com"
OWNER_USERNAME = "bharath_goud"
OWNER_PASSWORD = "Nakshatra#2025"
OWNER_NAME = "Bharath Goud"

# A rolling year ending today, so the data always covers the last 12 months.
START_DATE = datetime.date.today() - datetime.timedelta(days=365)
D = Decimal

# --------------------------------------------------------------------------
# What the shop sells. Each stream:
#   mean      average sale lines per day (before season/weekday factors)
#   bank      share of sales paid by UPI / card / EMI (rest is cash)
#   items     (sub-category, child or None, low, high, margin_low, margin_high, weight)
#   stock     (purchase category, purchase sub-category) the cost is restocked under,
#             or None when the sale has no stock cost (services)
# --------------------------------------------------------------------------
NEW_PHONES = {
    "Samsung": ([("Galaxy F15 5G", 11500, 13500), ("Galaxy A15 5G", 16000, 18500), ("Galaxy M35 5G", 17500, 20500),
                 ("Galaxy A35 5G", 28000, 32000), ("Galaxy S24 FE", 48000, 56000)], 22),
    "Redmi / Xiaomi": ([("Redmi A4", 7500, 8800), ("Redmi 13C", 8500, 10500), ("Redmi Note 13", 15000, 17500),
                        ("Redmi Note 14 Pro", 23000, 27000)], 20),
    "Realme": ([("Realme C63", 8800, 10500), ("Realme Narzo 70", 13000, 16000), ("Realme 13 Pro", 22000, 26000)], 12),
    "Vivo": ([("Vivo Y28", 11500, 13500), ("Vivo T3x", 12500, 14500), ("Vivo V40", 32000, 37000)], 14),
    "Oppo": ([("Oppo A3x", 10500, 12500), ("Oppo Reno 12", 33000, 38000)], 8),
    "Apple iPhone": ([("iPhone 13", 42000, 48000), ("iPhone 15", 62000, 69000), ("iPhone 16", 72000, 80000),
                      ("iPhone 16 Pro", 110000, 120000)], 10),
    "OnePlus": ([("OnePlus Nord CE4", 23000, 26000), ("OnePlus 12R", 38000, 42000)], 6),
    "Motorola": ([("Moto G45", 10500, 12500), ("Moto Edge 50", 24000, 28000)], 5),
}
USED_PHONES = {
    "Used iPhones": ([("iPhone XR", 11000, 14000), ("iPhone 11", 16000, 20000), ("iPhone 12", 24000, 29000),
                      ("iPhone 13", 33000, 38000)], 40),
    "Used Samsung": ([("Galaxy M31", 7000, 9000), ("Galaxy A52", 12000, 15000), ("Galaxy S21", 22000, 27000)], 30),
    "Used Redmi / Realme / Others": ([("Redmi Note 10", 7000, 9000), ("Realme 8", 6000, 8000),
                                      ("Vivo V21", 12000, 15000), ("OnePlus 9R", 20000, 24000)], 30),
}

ACCESSORIES = [  # name, lo, hi, margin
    ("Chargers & Cables", 300, 1800, (0.40, 0.55)), ("Earphones & Earbuds", 400, 4500, (0.30, 0.42)),
    ("Power Banks", 800, 2800, (0.25, 0.35)), ("Smart Watches & Bands", 1500, 7000, (0.22, 0.32)),
    ("Bluetooth Speakers", 900, 4500, (0.25, 0.35)), ("Memory Cards & Pen Drives", 350, 1200, (0.15, 0.25)),
    ("Phone Stands & Holders", 150, 600, (0.50, 0.60)),
]
COVERS = [
    ("Back Covers", 150, 600, (0.55, 0.65)), ("Flip Covers", 300, 900, (0.50, 0.60)),
    ("Pouches & Sleeves", 200, 700, (0.50, 0.60)), ("Premium & Designer Cases", 500, 1800, (0.45, 0.55)),
]
GUARDS = [
    ("Tempered Glass", 150, 500, (0.62, 0.72)), ("Privacy Glass", 250, 700, (0.60, 0.70)),
    ("UV / Curved Glass", 500, 1200, (0.55, 0.65)), ("Camera Lens Protector", 100, 300, (0.60, 0.70)),
    ("Hydrogel Screen Film", 200, 600, (0.58, 0.68)),
]
REPAIRS = [  # name, lo, hi, margin, spare-part sub-category
    ("Screen Replacement", 1800, 14000, (0.30, 0.40), "Displays & Touch Panels"),
    ("Battery Replacement", 900, 3500, (0.40, 0.50), "Batteries"),
    ("Charging Port Repair", 400, 1500, (0.55, 0.65), "Charging Ports & Flex Cables"),
    ("Water Damage Service", 1200, 4000, (0.50, 0.60), "Motherboard Parts & ICs"),
    ("Software Update & Flashing", 200, 800, (0.88, 0.95), None),
    ("Data Transfer & Backup", 100, 300, (0.92, 0.98), None),
]
RECHARGES = [("Mobile Recharge Commission", 60, 500), ("DTH Recharge Commission", 40, 300),
             ("Bill Payment Commission", 30, 200)]

# Purchase categories: name -> (gst %, sub-categories)
PURCHASES = {
    "New Mobiles Stock": (18, list(NEW_PHONES)),
    "Old Mobiles (Buyback)": (0, list(USED_PHONES)),
    "Accessories Stock": (18, [a[0] for a in ACCESSORIES]),
    "Pouches & Covers Stock": (18, [c[0] for c in COVERS]),
    "Screen Guards Stock": (18, [g[0] for g in GUARDS]),
    "Repair Spare Parts": (18, ["Displays & Touch Panels", "Batteries", "Charging Ports & Flex Cables",
                                "Motherboard Parts & ICs", "Repair Tools & Consumables"]),
}
PURCHASE_VENDOR = {
    "Samsung": "Sri Balaji Mobile Distributors", "Vivo": "Sri Balaji Mobile Distributors",
    "Oppo": "Sri Balaji Mobile Distributors", "Realme": "Sri Balaji Mobile Distributors",
    "Redmi / Xiaomi": "Ganesh Telecom Wholesale", "Motorola": "Ganesh Telecom Wholesale",
    "Apple iPhone": "Vasavi Mobile Traders", "OnePlus": "Vasavi Mobile Traders",
}
STOCK_VENDOR = {
    "Accessories Stock": ["Om Sai Accessories Hub", "Ganesh Telecom Wholesale"],
    "Pouches & Covers Stock": ["Kumar Cover World"],
    "Screen Guards Stock": ["GlassKing Screen Guards"],
    "Repair Spare Parts": ["Charminar Mobile Spares"],
}
RESTOCK_EVERY = {"New Mobiles Stock": 3, "Accessories Stock": 10, "Pouches & Covers Stock": 12,
                 "Screen Guards Stock": 12, "Repair Spare Parts": 9}

VENDORS = [
    ("Sri Balaji Mobile Distributors", "9848011001", "Samsung, Vivo, Oppo, Realme — credit 15 days"),
    ("Ganesh Telecom Wholesale", "9848011002", "Redmi, Motorola, accessories"),
    ("Vasavi Mobile Traders", "9848011003", "Apple & OnePlus — bank transfer"),
    ("Om Sai Accessories Hub", "9848011004", "Chargers, earbuds, power banks"),
    ("Kumar Cover World", "9848011005", "Back covers, flip covers, pouches"),
    ("GlassKing Screen Guards", "9848011006", "Tempered & privacy glass"),
    ("Charminar Mobile Spares", "9848011007", "Displays, batteries, flex cables"),
    ("Techno Bazaar Used Phones", "9848011008", "Bulk used-phone dealer"),
]

# Expenses -------------------------------------------------------------
SALARIES = {
    "Management": [("Naresh Goud", 35000)],
    "Sales Floor": [("Ravi Teja", 22000), ("Mahesh Yadav", 20000), ("Srinivas Reddy", 21000), ("Pooja Sharma", 19000)],
    "Repair Workshop": [("Imran Khan", 28000), ("Ajay Kumar", 24000)],
    "Support": [("Ramulu", 12000)],
}
EXPENSES = {
    "Rent": [],
    "Electricity & Utilities": ["Electricity", "Water", "Internet & Telephone"],
    "Marketing & Promotions": ["Digital Ads", "Flex & Hoardings", "Festival Offers", "Pamphlets & Local Ads"],
    "Staff Incentives & Bonus": ["Sales Incentives", "Festival Bonus"],
    "Shop Maintenance": ["AC & Electrical", "Furniture & Fixtures", "Cleaning"],
    "Transport & Courier": [],
    "Insurance Premium": ["Stock & Fire Insurance"],
    "Bank & Payment Charges": [],
    "Software & Subscriptions": [],
    "Staff Welfare": ["Tea & Snacks", "Festival Lunch"],
    "Taxes": ["GST Payment", "Professional Tax", "Income Tax Advance"],
    "Miscellaneous": [],
}

NEW_NOTES = ["", "", "", "Exchange offer", "EMI purchase", "Festival offer", "Free tempered glass", "Gift box combo"]
USED_NOTES = ["", "", "3-month shop warranty", "Exchange", "Bill & box available"]
GENERIC_NOTES = ["", "", "", "", "Combo with phone", "Walk-in"]


def price(rng, lo, hi, psychological=True):
    """A shelf-style price: 17,999 / 1,299 / 449 (or a round figure for services)."""
    v = rng.uniform(lo, hi)
    if not psychological:
        step = 100 if v >= 1500 else 50
        return int(round(v / step) * step)
    if v >= 5000:
        return int(round(v / 500) * 500 - 1)
    if v >= 1000:
        return int(round(v / 100) * 100 - 1)
    return int(round(v / 50) * 50 - 1)


class Command(BaseCommand):
    help = (
        "Drops the 'Nakshatra Mobiles' organization if present, signs it up fresh (owner: Bharath Goud) and "
        "backfills a rolling year of mobile-shop data: new/old phones, accessories, covers, screen guards, "
        "repairs, stock purchases, expenses, payroll, tax and cash/bank movement."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--until", type=datetime.date.fromisoformat, default=None,
            help="Last day to generate daily entries for (YYYY-MM-DD). Defaults to today.",
        )

    def handle(self, *args, **options):
        self.end_date = options["until"] or datetime.date.today()
        existing = Organization.objects.filter(name=ORG_NAME).first()
        if existing is not None:
            self.stdout.write(self.style.WARNING(
                f"Dropping '{ORG_NAME}' ({existing.organization_code}) and schema {existing.schema_name}..."
            ))
            delete_organization_and_tenant(existing)
        User.objects.filter(username=OWNER_USERNAME).delete()
        User.objects.filter(email=OWNER_EMAIL).delete()

        org, _owner = create_organization_with_tenant_schema_and_admin(
            org_data={
                "name": ORG_NAME,
                "business_type": Organization.BusinessType.RETAIL_ECOMMERCE,
                "size": Organization.OrganizationSize.SMALL,
                "industry": "Mobile phones & accessories retail",
                "contact_person": OWNER_NAME,
                "contact_email": OWNER_EMAIL,
                "contact_phone": "9848011000",
                "address": "Shop No. 12, Ameerpet Main Road",
                "city": "Hyderabad",
                "state": "Telangana",
                "country": "India",
            },
            admin_data={
                "email": OWNER_EMAIL,
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
                "first_name": "Bharath",
                "last_name": "Goud",
            },
        )
        plan = Plan.objects.filter(is_active=True).order_by("monthly_price").first()
        if plan is not None:
            billing_services.start_trial(org, plan)
        self.stdout.write(self.style.SUCCESS(f"Created {org.name} ({org.organization_code}), schema {org.schema_name}."))

        with schema_context(org.schema_name):
            self._seed()

        self.stdout.write(self.style.SUCCESS("\nDone. Login with:"))
        self.stdout.write("  URL:      http://127.0.0.1:8000/accounts/login/")
        self.stdout.write(f"  Username: {OWNER_USERNAME}  (or email {OWNER_EMAIL})")
        self.stdout.write(f"  Password: {OWNER_PASSWORD}")

    # ------------------------------------------------------------------
    def _seed(self):
        from apps.finance.models import (
            CashTransfer, Category, ExpenseEntry, FinanceSettings, Partner,
            PartnerTransaction, Payable, PaymentMode, PurchaseEntry, SalesEntry,
            Subcategory, Vendor,
        )

        today = datetime.date.today()
        end = max(self.end_date, today)      # last day that gets daily entries
        rng = random.Random(4242)
        OPENING_CASH, OPENING_BANK = D("150000"), D("800000")

        def money(v):
            return D(str(round(v, 2)))

        def mode(bank_share):
            return PaymentMode.BANK if rng.random() < bank_share else PaymentMode.CASH

        def count(mean):
            return max(0, int(round(rng.gauss(mean, math.sqrt(max(mean, 0.5))))))

        # --- Opening position -------------------------------------------------
        values = dict(fy_start_month=4, opening_balance=OPENING_CASH, opening_bank_balance=OPENING_BANK,
                      opening_date=START_DATE)
        fs = FinanceSettings.objects.first()
        if fs is None:
            FinanceSettings.objects.create(**values)
        else:
            for k, v in values.items():
                setattr(fs, k, v)
            fs.save()

        # --- Categories & sub-categories (a fresh schema still has generic starters) ---
        Category.objects.all().delete()
        sales_cat, sales_sub, sales_child = {}, {}, {}

        def make_sales_cat(name, gst):
            sales_cat[name] = Category.objects.create(kind=Category.Kind.SALES, name=name, gst_rate=gst)
            return sales_cat[name]

        for cname, catalog in (("New Mobiles", NEW_PHONES), ("Old Mobiles", USED_PHONES)):
            cat = make_sales_cat(cname, 18)
            for brand, (models, _w) in catalog.items():
                sub = Subcategory.objects.create(category=cat, name=brand)
                sales_sub[(cname, brand)] = sub
                for model, *_ in models:
                    sales_child[(cname, brand, model)] = Subcategory.objects.create(category=cat, parent=sub, name=model)
        for cname, rows in (("Accessories", ACCESSORIES), ("Pouches & Covers", COVERS), ("Screen Guards", GUARDS),
                            ("Repairs & Services", REPAIRS)):
            cat = make_sales_cat(cname, 18)
            for row in rows:
                sales_sub[(cname, row[0])] = Subcategory.objects.create(category=cat, name=row[0])
        cat = make_sales_cat("Recharge & Bill Payments", 0)
        for name, *_ in RECHARGES:
            sales_sub[("Recharge & Bill Payments", name)] = Subcategory.objects.create(category=cat, name=name)

        exp_cat, exp_sub = {}, {}
        salary_cat = Category.objects.create(kind=Category.Kind.EXPENSE, name="Salaries & Wages")
        salary_emp = {}
        for dept, staff in SALARIES.items():
            dsub = Subcategory.objects.create(category=salary_cat, name=dept)
            for name, _ in staff:
                salary_emp[(dept, name)] = Subcategory.objects.create(category=salary_cat, parent=dsub, name=name)
        for cname, subs in EXPENSES.items():
            exp_cat[cname] = Category.objects.create(kind=Category.Kind.EXPENSE, name=cname)
            for sname in subs:
                exp_sub[(cname, sname)] = Subcategory.objects.create(category=exp_cat[cname], name=sname)

        pur_cat, pur_sub = {}, {}
        for cname, (gst, subs) in PURCHASES.items():
            pur_cat[cname] = Category.objects.create(kind=Category.Kind.PURCHASE, name=cname, gst_rate=gst)
            for sname in subs:
                pur_sub[(cname, sname)] = Subcategory.objects.create(category=pur_cat[cname], name=sname)

        # --- Directory ----------------------------------------------------------
        for name, phone, details in VENDORS:
            Vendor.objects.create(
                name=name, phone=phone, details=details,
                opening_balance=D(rng.choice([0, 0, 25000, 60000])), opening_balance_as_on=START_DATE,
            )
        owner = Partner.objects.create(
            name=OWNER_NAME, phone="9848011000", opening_balance=D("2500000"), opening_balance_as_on=START_DATE,
        )
        # A second partner with no capital on record yet; investments are logged against him as they happen.
        Partner.objects.create(name="Bharath Rao", opening_balance=D("0"), opening_balance_as_on=START_DATE)

        # --- Daily trading ----------------------------------------------------------
        sales, purchases, expenses, payables = [], [], [], []
        partner_txns, transfers = [], []
        pools = {}               # (purchase category, sub) -> [cost, units] awaiting restock
        month_sales, month_gp, month_bank_sales = {}, {}, {}
        units_sold = {}

        def season(day):
            md = (day.month, day.day)
            if (10, 10) <= md <= (11, 5):
                s = 1.7                     # Dussehra-Diwali
            elif (9, 20) <= md < (10, 10):
                s = 1.25
            elif (1, 10) <= md <= (1, 16):
                s = 1.25                    # Sankranti
            elif (3, 15) <= md <= (3, 23):
                s = 1.2                     # Ugadi / Eid
            elif (12, 20) <= md:
                s = 1.15
            elif (8, 5) <= md <= (8, 15):
                s = 1.15
            elif day.month in (6, 7):
                s = 0.85
            elif day.month == 2:
                s = 0.95
            else:
                s = 1.0
            return s

        def add_sale(day, cname, sub_name, child_name, unit, qty, margin, bank_share, note):
            amount = D(unit) * qty
            child = sales_child.get((cname, sub_name, child_name)) if child_name else None
            sales.append(SalesEntry(
                date=day, channel=sales_cat[cname], subcategory=child or sales_sub[(cname, sub_name)],
                quantity=qty, amount=amount, payment_mode=mode(bank_share), note=note,
            ))
            key = (day.year, day.month)
            month_sales[key] = month_sales.get(key, D(0)) + amount
            month_gp[key] = month_gp.get(key, D(0)) + amount * D(str(round(margin, 4)))
            units_sold[cname] = units_sold.get(cname, 0) + qty
            return amount

        def pool_cost(pcat, psub, cost, units):
            p = pools.setdefault((pcat, psub), [D(0), 0])
            p[0] += cost
            p[1] += units

        day = START_DATE
        while day <= end:
            months = (day.year - START_DATE.year) * 12 + (day.month - START_DATE.month)
            factor = season(day) * (1 + months * 0.008)
            if day.weekday() >= 5:
                factor *= 1.25
            elif day.weekday() == 0:
                factor *= 0.9
            if day.day <= 7:
                factor *= 1.1
            iphone_boost = 1.8 if (9, 20) <= (day.month, day.day) or (day.month == 10) else 1.0

            # New mobiles
            brands = list(NEW_PHONES)
            weights = [NEW_PHONES[b][1] * (iphone_boost if b == "Apple iPhone" else 1) for b in brands]
            for _ in range(count(2.6 * factor)):
                brand = rng.choices(brands, weights)[0]
                model, lo, hi = rng.choice(NEW_PHONES[brand][0])
                unit = price(rng, lo, hi)
                qty = 2 if rng.random() < 0.08 else 1
                margin = rng.uniform(0.035, 0.08) if brand != "Apple iPhone" else rng.uniform(0.02, 0.04)
                amount = add_sale(day, "New Mobiles", brand, model, unit, qty, margin, 0.72,
                                  rng.choice(NEW_NOTES))
                pool_cost("New Mobiles Stock", brand, amount * D(str(1 - margin)), qty)

            # Old (pre-owned) mobiles: sold, and bought back from walk-in customers
            groups = list(USED_PHONES)
            gweights = [USED_PHONES[g][1] for g in groups]
            for _ in range(count(1.3 * factor)):
                grp = rng.choices(groups, gweights)[0]
                model, lo, hi = rng.choice(USED_PHONES[grp][0])
                add_sale(day, "Old Mobiles", grp, model, price(rng, lo, hi), 1, rng.uniform(0.12, 0.22), 0.5,
                         rng.choice(USED_NOTES))
            for _ in range(count(1.3 * factor)):
                grp = rng.choices(groups, gweights)[0]
                model, lo, hi = rng.choice(USED_PHONES[grp][0])
                buy = D(price(rng, lo, hi, psychological=False)) * D(str(round(rng.uniform(0.78, 0.88), 3)))
                purchases.append(PurchaseEntry(
                    date=day, category=pur_cat["Old Mobiles (Buyback)"],
                    subcategory=pur_sub[("Old Mobiles (Buyback)", grp)],
                    vendor=rng.choice(["Walk-in customer (buyback)"] * 6 + ["Techno Bazaar Used Phones"]),
                    quantity=1, amount=D(int(buy // 50 * 50)), payment_mode=mode(0.45), note=f"{model} buyback",
                ))

            # Accessories, covers, screen guards (units counted; cost restocked in batches)
            for cname, rows, mean, bank, pcat in (
                ("Accessories", ACCESSORIES, 8.0, 0.62, "Accessories Stock"),
                ("Pouches & Covers", COVERS, 6.0, 0.55, "Pouches & Covers Stock"),
                ("Screen Guards", GUARDS, 7.0, 0.50, "Screen Guards Stock"),
            ):
                for _ in range(count(mean * factor)):
                    name, lo, hi, (mlo, mhi) = rng.choice(rows)
                    unit = price(rng, lo, hi)
                    qty = rng.choices([1, 2, 3, 4], [60, 25, 10, 5])[0]
                    margin = rng.uniform(mlo, mhi)
                    amount = add_sale(day, cname, name, None, unit, qty, margin, bank,
                                      rng.choice(GENERIC_NOTES))
                    pool_cost(pcat, name, amount * D(str(1 - margin)), qty)

            # Repairs & services (spare-part cost restocked in batches)
            for _ in range(count(3.0 * factor)):
                name, lo, hi, (mlo, mhi), spare = rng.choice(REPAIRS)
                margin = rng.uniform(mlo, mhi)
                amount = add_sale(day, "Repairs & Services", name, None, price(rng, lo, hi, psychological=False), 1,
                                  margin, 0.5, rng.choice(["", "", "Same-day delivery", "Warranty 3 months"]))
                if spare:
                    pool_cost("Repair Spare Parts", spare, amount * D(str(1 - margin)), 1)

            # Recharge & bill-payment commissions
            for _ in range(count(1.6)):
                name, lo, hi = rng.choice(RECHARGES)
                add_sale(day, "Recharge & Bill Payments", name, None, price(rng, lo, hi, psychological=False), 1,
                         1.0, 0.9, "")

            # ---- Stock restocking, derived from what was sold ----
            for pcat, every in RESTOCK_EVERY.items():
                if (day - START_DATE).days % every != 0:
                    continue
                for (pc, psub), (cost, units) in list(pools.items()):
                    if pc != pcat or cost <= 0:
                        continue
                    amt = money(float(cost) * rng.uniform(0.96, 1.08))
                    amt = D(int(amt // 10 * 10))
                    qty = max(1, int(round(units * rng.uniform(0.95, 1.15))))
                    vendor = PURCHASE_VENDOR[psub] if pcat == "New Mobiles Stock" else rng.choice(STOCK_VENDOR[pcat])
                    on_credit = pcat == "New Mobiles Stock" and psub != "Apple iPhone" and rng.random() < 0.3
                    if on_credit:
                        payable = Payable(
                            vendor=vendor, bill_date=day, due_date=day + datetime.timedelta(days=15), amount=amt,
                            note=f"{pcat} · {psub} · Qty {qty}",
                        )
                        pay_date = day + datetime.timedelta(days=rng.randint(9, 18))
                        roll = rng.random()
                        payments = []
                        if pay_date <= end and roll < 0.92:
                            paid = amt if roll < 0.75 else money(float(amt) * 0.5)
                            payments.append(PurchaseEntry(
                                date=pay_date, vendor=vendor, amount=paid, payment_mode=PaymentMode.BANK,
                                note=f"Payment to {vendor} for bill",
                            ))
                            payable.amount_paid = paid
                        payables.append((payable, payments))
                    else:
                        purchases.append(PurchaseEntry(
                            date=day, category=pur_cat[pcat], subcategory=pur_sub[(pcat, psub)], vendor=vendor,
                            quantity=qty, amount=amt,
                            payment_mode=PaymentMode.BANK if pcat == "New Mobiles Stock" else mode(0.6),
                            note=rng.choice(["", "", "Reorder", "Weekly stock"]),
                        ))
                    pools[(pc, psub)] = [D(0), 0]

            # ---- Monthly fixed costs ----
            prev = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)

            def exp(cname, sname, amount, pmode=PaymentMode.BANK, note="", sub=None):
                expenses.append(ExpenseEntry(
                    date=day, category=exp_cat[cname] if cname in exp_cat else salary_cat,
                    subcategory=sub or exp_sub.get((cname, sname)), amount=money(amount),
                    payment_mode=pmode, note=note,
                ))

            if day.day == 1:
                exp("Rent", None, 85000, note="Shop rent")
                for dept, staff in SALARIES.items():
                    for name, sal in staff:
                        exp("Salaries & Wages", None, sal * (1 + months * 0.004), note=f"{dept} salary",
                            sub=salary_emp[(dept, name)])
                exp("Software & Subscriptions", None, 1500, note="Billing software")
                if day.month in (1, 4, 7, 10):
                    exp("Insurance Premium", "Stock & Fire Insurance", 16500, note="Quarterly premium")
                if month_sales.get(prev):
                    exp("Staff Incentives & Bonus", "Sales Incentives", float(month_sales[prev]) * 0.005,
                        note="Monthly sales incentive")
            if day.day == 5:
                exp("Electricity & Utilities", "Electricity",
                    rng.uniform(9000, 12500) + (5500 if day.month in (4, 5, 6) else 0))
                exp("Electricity & Utilities", "Internet & Telephone", 1800)
                exp("Electricity & Utilities", "Water", 800, PaymentMode.CASH)
                exp("Taxes", "Professional Tax", 1600)
            if day.day == 10 and month_gp.get(prev):
                exp("Taxes", "GST Payment", float(month_gp[prev]) * 18 / 118 * 0.85, note="Net GST for previous month")
            if day.day == 12 and month_sales.get(prev):
                exp("Bank & Payment Charges", None, float(month_sales[prev]) * 0.6 * 0.004, note="UPI / card MDR")
            if day.day == 3:
                exp("Marketing & Promotions", "Digital Ads", rng.uniform(11000, 18000), note="Google & Instagram ads")
            if (day.month, day.day) in ((3, 15), (6, 15), (9, 15), (12, 15)):
                exp("Taxes", "Income Tax Advance", rng.uniform(90000, 140000), note="Quarterly advance tax")
            if (day.month, day.day) == (10, 12):
                exp("Marketing & Promotions", "Festival Offers", rng.uniform(40000, 60000), note="Diwali campaign")
                exp("Marketing & Promotions", "Flex & Hoardings", rng.uniform(15000, 25000), note="Festival banners")
            if (day.month, day.day) == (10, 24):
                exp("Staff Incentives & Bonus", "Festival Bonus", 62000, note="Diwali bonus")
                exp("Staff Welfare", "Festival Lunch", 9500, PaymentMode.CASH, note="Diwali staff lunch")
            if (day.month, day.day) in ((1, 12), (3, 17)):
                exp("Marketing & Promotions", "Festival Offers", rng.uniform(12000, 22000), note="Festival offer promotion")

            # ---- Ad-hoc shop expenses ----
            for _ in range(rng.choice([0, 1, 1, 2, 2])):
                cname, sname, lo, hi = rng.choice([
                    ("Staff Welfare", "Tea & Snacks", 150, 900), ("Miscellaneous", None, 100, 2500),
                    ("Transport & Courier", None, 150, 1800), ("Shop Maintenance", "Cleaning", 200, 1200),
                    ("Shop Maintenance", "AC & Electrical", 500, 4500), ("Marketing & Promotions", "Pamphlets & Local Ads", 300, 2500),
                ])
                exp(cname, sname, rng.uniform(lo, hi), mode(0.25), note=rng.choice(["", "", "Urgent"]))

            # ---- Owner drawings, 5th of each month ----
            if day.day == 5 and day != START_DATE:
                partner_txns.append(PartnerTransaction(
                    partner=owner, date=day, kind="WITHDRAWAL",
                    amount=D(rng.choice([120000, 130000, 150000, 160000, 180000])), payment_mode=PaymentMode.BANK,
                    note="Owner drawing",
                ))
            day += datetime.timedelta(days=1)

        # --- Extra supplier bills (no customer credit: every sale is a general counter sale) ---
        for vendor, age, due_in, amt, paid, note in [
            ("Sri Balaji Mobile Distributors", 12, 3, 385000, 0, "Samsung / Vivo stock, credit bill"),
            ("Ganesh Telecom Wholesale", 30, -12, 214000, 100000, "Redmi stock bill overdue"),
            ("Om Sai Accessories Hub", 18, 12, 46500, 0, "Chargers & earbuds"),
        ]:
            payable = Payable(
                vendor=vendor, bill_date=today - datetime.timedelta(days=age),
                due_date=today + datetime.timedelta(days=due_in), amount=D(amt), amount_paid=D(paid), note=note,
            )
            payments = []
            if paid:
                payments.append(PurchaseEntry(
                    date=payable.bill_date + datetime.timedelta(days=5), vendor=vendor, amount=D(paid),
                    payment_mode=PaymentMode.BANK, note=f"Payment to {vendor} for bill",
                ))
            payables.append((payable, payments))

        # --- Cash & bank movement: derive deposits/withdrawals/top-ups from the books ----
        flows = {}       # date -> [cash change, bank change]

        def flow(d, cash=D(0), bank=D(0)):
            f = flows.setdefault(d, [D(0), D(0)])
            f[0] += cash
            f[1] += bank

        payment_purchases = [p for _, pays in payables for p in pays]
        for s in sales:
            flow(s.date, **({"cash": s.amount} if s.payment_mode == PaymentMode.CASH else {"bank": s.amount}))
        for row in purchases + payment_purchases + expenses:
            flow(row.date, **({"cash": -row.amount} if row.payment_mode == PaymentMode.CASH else {"bank": -row.amount}))
        for t in partner_txns:
            sign = 1 if t.kind == "INVESTMENT" else -1
            flow(t.date, **({"cash": sign * t.amount} if t.payment_mode == PaymentMode.CASH else {"bank": sign * t.amount}))

        cash_bal, bank_bal = OPENING_CASH, OPENING_BANK
        topups = 0
        d = START_DATE
        while d <= end:
            c, b = flows.get(d, [D(0), D(0)])
            cash_bal += c
            bank_bal += b
            if cash_bal < D("20000"):                       # refill the till from the bank
                need = D(int(math.ceil(float(D("60000") - cash_bal) / 5000) * 5000))
                transfers.append(CashTransfer(date=d, direction=CashTransfer.Direction.BANK_TO_CASH, amount=need,
                                              note="Cash withdrawn for till / buybacks"))
                cash_bal += need
                bank_bal -= need
            elif cash_bal > D("180000") and d.weekday() in (1, 4):   # bank the excess cash Tue / Fri
                dep = D(int((cash_bal - D("70000")) // 5000 * 5000))
                transfers.append(CashTransfer(date=d, direction=CashTransfer.Direction.CASH_TO_BANK, amount=dep,
                                              note="Cash deposit"))
                cash_bal -= dep
                bank_bal += dep
            if bank_bal < D("100000"):                      # the owner tops up when the bank runs low
                amt = D(int(math.ceil(float(D("400000") - bank_bal) / 50000) * 50000))
                partner_txns.append(PartnerTransaction(
                    partner=owner, date=d, kind="INVESTMENT", amount=amt, payment_mode=PaymentMode.BANK,
                    note="Working capital top-up",
                ))
                bank_bal += amt
                topups += 1
            d += datetime.timedelta(days=1)

        SalesEntry.objects.bulk_create(sales, batch_size=500)
        ExpenseEntry.objects.bulk_create(expenses, batch_size=500)
        PurchaseEntry.objects.bulk_create(purchases, batch_size=500)
        CashTransfer.objects.bulk_create(transfers, batch_size=500)
        PartnerTransaction.objects.bulk_create(partner_txns)
        for payable, payments in payables:
            payable.save()
            PurchaseEntry.objects.bulk_create(payments)

        revenue = sum((s.amount for s in sales), D(0))
        stock = sum((p.amount for p in purchases + payment_purchases), D(0))
        spend = sum((e.amount for e in expenses), D(0))
        self.stdout.write(
            f"  {len(sales)} sales lines, {len(purchases)} purchases "
            f"(+{len(payables)} credit bills), {len(expenses)} expenses, {len(transfers)} cash/bank transfers, "
            f"{len(partner_txns)} owner transactions ({topups} capital top-ups)."
        )
        self.stdout.write("  Units sold: " + ", ".join(f"{k} {v}" for k, v in units_sold.items()))
        self.stdout.write(
            f"  Revenue ₹{revenue:,.0f} | stock bought ₹{stock:,.0f} | expenses ₹{spend:,.0f} | "
            f"operating result ₹{revenue - stock - spend:,.0f} | closing cash ₹{cash_bal:,.0f} bank ₹{bank_bal:,.0f}."
        )
