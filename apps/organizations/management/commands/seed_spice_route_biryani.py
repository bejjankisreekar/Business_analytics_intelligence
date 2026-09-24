"""Seed "Spice Route Biryani House" — a Hyderabad biryani restaurant — with
daily trading data from 01-01-2025 onward: a full biryani-led menu (biryanis,
starters, curries, breads & rice, beverages, desserts, combos), the matching
raw-material purchases (rice, meat, vegetables, dairy, spices, gas, packaging,
beverages stock), staff payroll, rent, utilities, delivery-platform (Swiggy /
Zomato) commissions, tax and cash/bank movement.

Every menu sale carries a unit count (`quantity`), so daily "how many
biryanis" (and every other item) is a straight SUM(quantity) query against
SalesEntry filtered by subcategory — printed as a summary at the end of this
command.
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

ORG_NAME = "Spice Route Biryani House"
OWNER_EMAIL = "farhan@spiceroutebiryani.com"
OWNER_USERNAME = "farhan_baig"
OWNER_PASSWORD = "SpiceRoute#2025"
OWNER_NAME = "Farhan Baig"

START_DATE = datetime.date(2025, 1, 1)
D = Decimal

# --------------------------------------------------------------------------
# Menu. Each dict: item name -> (low price, high price[, weight]).
# Biryanis carry an explicit weight (popularity share); everything else is
# picked uniformly from its category.
# --------------------------------------------------------------------------
BIRYANIS = {
    "Chicken Biryani": (220, 260, 38),
    "Mutton Biryani": (320, 380, 22),
    "Veg Biryani": (160, 190, 12),
    "Egg Biryani": (140, 170, 8),
    "Paneer Biryani": (190, 220, 6),
    "Prawns Biryani": (280, 320, 5),
    "Fish Biryani": (260, 300, 4),
    "Chef's Special Biryani": (360, 420, 8),
    "Family Pack Biryani (serves 4)": (950, 1150, 6),
}
STARTERS = {
    "Chicken 65": (180, 220), "Chilli Chicken": (190, 230), "Mutton Fry": (260, 300),
    "Prawns Fry": (280, 320), "Paneer Tikka": (190, 220), "Veg Manchurian": (150, 180),
    "Seekh Kebab": (220, 260),
}
CURRIES = {
    "Chicken Curry": (180, 220), "Mutton Curry": (280, 330), "Paneer Butter Masala": (200, 240),
    "Dal Tadka": (120, 150), "Egg Curry": (140, 170),
}
BREADS_RICE = {
    "Butter Naan": (35, 45), "Tandoori Roti": (20, 30), "Jeera Rice": (120, 150), "Ghee Rice": (130, 160),
}
BEVERAGES = {
    "Soft Drinks": (40, 60), "Mineral Water": (20, 30), "Sweet Lassi": (60, 80),
    "Buttermilk": (30, 45), "Fresh Fruit Juice": (70, 100), "Mocktails": (90, 130),
}
DESSERTS = {
    "Double Ka Meetha": (80, 110), "Gulab Jamun (2 pcs)": (60, 80), "Ice Cream": (50, 70),
    "Sheer Khurma": (70, 90),
}
COMBOS = {
    "Biryani Combo Meal (Biryani + Drink + Dessert)": (280, 330),
}

# Purchase categories: name -> (gst %, sub-categories, representative unit cost for quantity back-out)
PURCHASES = {
    "Rice & Grains": (5, ["Basmati Rice", "Sona Masoori Rice"], 65),
    "Meat & Poultry": (2, ["Chicken", "Mutton", "Fish", "Prawns"], 320),
    "Vegetables & Fruits": (0, ["Onions", "Tomatoes", "Potatoes", "Mixed Vegetables", "Coriander & Mint"], 35),
    "Dairy & Paneer": (5, ["Milk", "Curd", "Ghee", "Paneer", "Cheese"], 90),
    "Spices & Masalas": (5, ["Biryani Masala", "Garam Masala", "Red Chilli Powder", "Turmeric Powder",
                             "Biryani Attar & Food Colour"], 220),
    "Cooking Oil & Fuel": (5, ["Sunflower Oil", "LPG Commercial Cylinder"], 140),
    "Packaging Material": (12, ["Biryani Parcel Boxes", "Carry Bags", "Aluminium Foil", "Napkins & Disposables"], 8),
    "Beverages Stock": (12, ["Soft Drink Crates", "Mineral Water Bottles", "Juice Concentrate"], 350),
}
# revenue share funneled into each purchase category, and how many days between restock buys
# (sums to ~33% of revenue — a normal restaurant food-cost ratio)
PURCHASE_SHARE = {
    "Rice & Grains": 0.06, "Meat & Poultry": 0.13, "Vegetables & Fruits": 0.045, "Dairy & Paneer": 0.025,
    "Spices & Masalas": 0.02, "Cooking Oil & Fuel": 0.018, "Packaging Material": 0.022, "Beverages Stock": 0.015,
}
RESTOCK_EVERY = {
    "Rice & Grains": 3, "Meat & Poultry": 1, "Vegetables & Fruits": 1, "Dairy & Paneer": 2,
    "Spices & Masalas": 7, "Cooking Oil & Fuel": 4, "Packaging Material": 5, "Beverages Stock": 6,
}
PURCHASE_VENDOR = {
    "Rice & Grains": "Deccan Rice & Grains Traders", "Meat & Poultry": "Al Barkath Meat & Poultry Suppliers",
    "Vegetables & Fruits": "Bowenpally Wholesale Vegetable Market", "Dairy & Paneer": "Sri Krishna Dairy Suppliers",
    "Spices & Masalas": "Hyderabad Spice Wholesale Mart", "Cooking Oil & Fuel": "Bharat Gas Agency",
    "Packaging Material": "Deccan Packaging Solutions", "Beverages Stock": "Bevco Beverages Distributors",
}
VENDORS = [
    ("Deccan Rice & Grains Traders", "9849022001", "Basmati & Sona Masoori rice — credit 10 days"),
    ("Al Barkath Meat & Poultry Suppliers", "9849022002", "Daily fresh chicken, mutton, fish, prawns"),
    ("Bowenpally Wholesale Vegetable Market", "9849022003", "Daily vegetables & fruits, cash only"),
    ("Sri Krishna Dairy Suppliers", "9849022004", "Milk, curd, ghee, paneer"),
    ("Hyderabad Spice Wholesale Mart", "9849022005", "Biryani masala & spice blends"),
    ("Bharat Gas Agency", "9849022006", "Commercial LPG cylinders"),
    ("Deccan Packaging Solutions", "9849022007", "Parcel boxes, bags, disposables"),
    ("Bevco Beverages Distributors", "9849022008", "Soft drinks, water, juice concentrate"),
]

SALARIES = {
    "Kitchen & Chefs": [("Chef Abdul Rahman", 34000), ("Sous Chef Imran", 24000), ("Tandoor Cook Yusuf", 19000),
                        ("Kitchen Helper Raju", 14000), ("Kitchen Helper Sandeep", 14000)],
    "Service Staff": [("Captain Naveed", 17000), ("Waiter Ramesh", 13000), ("Waiter Suresh", 13000),
                       ("Cashier Priya", 15500)],
    "Delivery & Packing": [("Delivery Rider Kiran", 12000), ("Delivery Rider Raju", 12000),
                            ("Packing Staff Anitha", 11000)],
    "Cleaning & Support": [("Cleaner Lakshmi", 9500)],
}
EXPENSES = {
    "Rent": [],
    "Electricity & Utilities": ["Electricity", "Water", "Internet & Telephone"],
    "Marketing & Promotions": ["Instagram & Facebook Ads", "Festival Offers", "Flex & Hoardings", "Pamphlets & Local Ads"],
    "Delivery Platform Commission": ["Swiggy Commission", "Zomato Commission"],
    "Staff Incentives & Bonus": ["Sales Incentives", "Festival Bonus"],
    "Shop Maintenance": ["Kitchen Equipment", "AC & Electrical", "Pest Control", "Cleaning Supplies"],
    "Transport & Courier": [],
    "Insurance Premium": ["Shop & Fire Insurance"],
    "Bank & Payment Charges": [],
    "Software & Subscriptions": ["POS Software", "Swiggy / Zomato Listing Fee"],
    "Staff Welfare": ["Staff Meals", "Festival Lunch"],
    "Taxes": ["GST Payment", "Professional Tax", "Income Tax Advance"],
    "Miscellaneous": [],
}

DINEIN_NOTES = ["", "", "", "Table order", "Weekend rush"]
TAKEAWAY_NOTES = ["", "", "Takeaway", "Phone order"]
SWIGGY_NOTES = ["Swiggy order"]
ZOMATO_NOTES = ["Zomato order"]


def price(rng, lo, hi):
    """A shelf-style menu price: 249 / 45 / 1099."""
    v = rng.uniform(lo, hi)
    if v >= 500:
        return int(round(v / 50) * 50 - 1)
    if v >= 100:
        return int(round(v / 10) * 10 - 1)
    return int(round(v / 5) * 5)


class Command(BaseCommand):
    help = (
        "Drops the 'Spice Route Biryani House' organization if present, signs it up fresh "
        "(owner: Farhan Baig) and backfills daily restaurant trading data from 01-01-2025: "
        "biryanis, starters, curries, breads & rice, beverages, desserts, combos, matching raw "
        "material purchases, payroll, rent, utilities, delivery commissions, tax and cash/bank movement."
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
                "business_type": Organization.BusinessType.RESTAURANTS_FOOD,
                "size": Organization.OrganizationSize.SMALL,
                "industry": "Restaurant — Hyderabadi biryani & multi-cuisine",
                "contact_person": OWNER_NAME,
                "contact_email": OWNER_EMAIL,
                "contact_phone": "9849022000",
                "address": "Plot No. 45, Tolichowki Main Road",
                "city": "Hyderabad",
                "state": "Telangana",
                "country": "India",
            },
            admin_data={
                "email": OWNER_EMAIL,
                "username": OWNER_USERNAME,
                "password": OWNER_PASSWORD,
                "first_name": "Farhan",
                "last_name": "Baig",
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

        end = max(self.end_date, START_DATE)
        rng = random.Random(8181)
        OPENING_CASH, OPENING_BANK = D("100000"), D("500000")

        def money(v):
            return D(str(round(v, 2)))

        def mode(bank_share):
            return PaymentMode.BANK if rng.random() < bank_share else PaymentMode.CASH

        def order_channel():
            """(note, bank_share) — dine-in / takeaway pay mixed, delivery apps settle to bank."""
            roll = rng.random()
            if roll < 0.35:
                return rng.choice(DINEIN_NOTES), 0.45
            if roll < 0.50:
                return rng.choice(TAKEAWAY_NOTES), 0.55
            if roll < 0.78:
                return rng.choice(SWIGGY_NOTES), 0.97
            return rng.choice(ZOMATO_NOTES), 0.97

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

        # --- Categories & sub-categories ---
        Category.objects.all().delete()
        sales_cat, sales_sub = {}, {}

        def make_sales_cat(name, gst):
            sales_cat[name] = Category.objects.create(kind=Category.Kind.SALES, name=name, gst_rate=gst)
            return sales_cat[name]

        cat = make_sales_cat("Biryanis", 5)
        for name in BIRYANIS:
            sales_sub[("Biryanis", name)] = Subcategory.objects.create(category=cat, name=name)
        for cname, rows, gst in (
            ("Starters & Appetizers", STARTERS, 5), ("Curries & Gravies", CURRIES, 5),
            ("Breads & Rice", BREADS_RICE, 5), ("Beverages", BEVERAGES, 12),
            ("Desserts", DESSERTS, 5), ("Combos & Family Meals", COMBOS, 5),
        ):
            cat = make_sales_cat(cname, gst)
            for name in rows:
                sales_sub[(cname, name)] = Subcategory.objects.create(category=cat, name=name)

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
        for cname, (gst, subs, _unit) in PURCHASES.items():
            pur_cat[cname] = Category.objects.create(kind=Category.Kind.PURCHASE, name=cname, gst_rate=gst)
            for sname in subs:
                pur_sub[(cname, sname)] = Subcategory.objects.create(category=pur_cat[cname], name=sname)

        # --- Directory ----------------------------------------------------------
        for name, phone, details in VENDORS:
            Vendor.objects.create(
                name=name, phone=phone, details=details,
                opening_balance=D(rng.choice([0, 0, 15000, 30000])), opening_balance_as_on=START_DATE,
            )
        owner = Partner.objects.create(
            name=OWNER_NAME, phone="9849022000", opening_balance=D("1200000"), opening_balance_as_on=START_DATE,
        )
        Partner.objects.create(name="Shahid Ali", opening_balance=D("0"), opening_balance_as_on=START_DATE)

        # --- Daily trading ----------------------------------------------------------
        sales, purchases, expenses, payables = [], [], [], []
        partner_txns, transfers = [], []
        month_sales, month_gp = {}, {}
        month_platform_rev = {"Swiggy Commission": {}, "Zomato Commission": {}}
        revenue_accum = {cname: D(0) for cname in PURCHASES}
        units_sold = {}

        def season(day):
            md = (day.month, day.day)
            if (12, 28) <= md or md <= (1, 2):
                s = 1.5                      # New Year
            elif (1, 10) <= md <= (1, 16):
                s = 1.25                     # Sankranti
            elif (3, 10) <= md <= (4, 5):
                s = 1.35                     # Ramzan / Eid / Ugadi window
            elif (6, 1) <= md <= (6, 10):
                s = 1.2                      # Eid-al-Adha window
            elif (8, 10) <= md <= (8, 17):
                s = 1.15                     # Independence Day week
            elif (10, 10) <= md <= (11, 5):
                s = 1.4                      # Dussehra-Diwali
            elif (12, 20) <= md <= (12, 27):
                s = 1.3                      # Christmas / year-end
            elif day.month in (6, 7):
                s = 0.85                     # monsoon dip
            elif day.month == 2:
                s = 0.95
            else:
                s = 1.0
            return s

        def add_sale(day, cname, name, lo, hi, qty_weights, note, bank_share):
            unit = price(rng, lo, hi)
            qty = rng.choices([1, 2, 3], qty_weights)[0]
            amount = D(unit) * qty
            sales.append(SalesEntry(
                date=day, channel=sales_cat[cname], subcategory=sales_sub[(cname, name)],
                quantity=qty, amount=amount, payment_mode=mode(bank_share), note=note,
            ))
            key = (day.year, day.month)
            month_sales[key] = month_sales.get(key, D(0)) + amount
            month_gp[key] = month_gp.get(key, D(0)) + amount * D("0.65")   # ~65% gross margin after raw material cost
            units_sold[name] = units_sold.get(name, 0) + qty
            return amount

        day = START_DATE
        while day <= end:
            months = (day.year - START_DATE.year) * 12 + (day.month - START_DATE.month)
            factor = season(day) * (1 + months * 0.01)
            wd = day.weekday()
            if wd == 4:
                factor *= 1.15       # Friday
            elif wd == 5:
                factor *= 1.3        # Saturday
            elif wd == 6:
                factor *= 1.35       # Sunday
            elif wd == 0:
                factor *= 0.9        # Monday dip

            day_revenue = D(0)

            # Biryanis — the flagship line
            names = list(BIRYANIS)
            weights = [BIRYANIS[n][2] for n in names]
            for _ in range(max(1, int(round(rng.gauss(55 * factor, math.sqrt(55 * factor)))))):
                name = rng.choices(names, weights)[0]
                lo, hi, _w = BIRYANIS[name]
                qw = [3, 5, 1] if "Family Pack" not in name else [85, 13, 2]
                note, bank_share = order_channel()
                day_revenue += add_sale(day, "Biryanis", name, lo, hi, qw, note, bank_share)

            # Everything else on the menu
            for cname, rows, mean in (
                ("Starters & Appetizers", STARTERS, 25.0), ("Curries & Gravies", CURRIES, 18.0),
                ("Breads & Rice", BREADS_RICE, 30.0), ("Beverages", BEVERAGES, 35.0),
                ("Desserts", DESSERTS, 12.0), ("Combos & Family Meals", COMBOS, 8.0),
            ):
                for _ in range(max(0, int(round(rng.gauss(mean * factor, math.sqrt(mean)))))):
                    name = rng.choice(list(rows))
                    lo, hi = rows[name]
                    note, bank_share = order_channel()
                    day_revenue += add_sale(day, cname, name, lo, hi, [70, 25, 5], note, bank_share)

            for cname, share in PURCHASE_SHARE.items():
                revenue_accum[cname] += day_revenue * D(str(share))

            # ---- Raw-material restocking, scaled to what's actually being sold ----
            for pcat, every in RESTOCK_EVERY.items():
                if (day - START_DATE).days % every != 0:
                    continue
                cost = revenue_accum[pcat]
                if cost <= 0:
                    continue
                amt = money(float(cost) * rng.uniform(0.94, 1.10))
                amt = D(int(amt // 10 * 10)) if amt > 10 else D(10)
                gst, subs, unit_cost = PURCHASES[pcat]
                sname = rng.choice(subs)
                qty = max(1, int(round(float(amt) / unit_cost)))
                purchases.append(PurchaseEntry(
                    date=day, category=pur_cat[pcat], subcategory=pur_sub[(pcat, sname)],
                    vendor=PURCHASE_VENDOR[pcat], quantity=qty, amount=amt,
                    payment_mode=mode(0.55 if pcat != "Vegetables & Fruits" else 0.1),
                    note=rng.choice(["", "", "Daily supply", "Weekly restock"]),
                ))
                revenue_accum[pcat] = D(0)

            # ---- Monthly fixed costs ----
            prev = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)

            def exp(cname, sname, amount, pmode=PaymentMode.BANK, note="", sub=None):
                expenses.append(ExpenseEntry(
                    date=day, category=exp_cat[cname] if cname in exp_cat else salary_cat,
                    subcategory=sub or exp_sub.get((cname, sname)), amount=money(amount),
                    payment_mode=pmode, note=note,
                ))

            if day.day == 1:
                exp("Rent", None, 65000, note="Restaurant + kitchen rent")
                for dept, staff in SALARIES.items():
                    for name, sal in staff:
                        exp("Salaries & Wages", None, sal * (1 + months * 0.004), note=f"{dept} salary",
                            sub=salary_emp[(dept, name)])
                exp("Software & Subscriptions", "POS Software", 1200, note="Billing/POS software")
                exp("Software & Subscriptions", "Swiggy / Zomato Listing Fee", 2500, note="Monthly listing fee")
                if day.month in (1, 4, 7, 10):
                    exp("Insurance Premium", "Shop & Fire Insurance", 11000, note="Quarterly premium")
                if month_sales.get(prev):
                    exp("Staff Incentives & Bonus", "Sales Incentives", float(month_sales[prev]) * 0.004,
                        note="Monthly sales incentive")
            if day.day == 2 and month_sales.get(prev):
                # delivery-platform commissions on the previous month's revenue, ~ half of orders are delivery
                delivery_rev = float(month_sales[prev]) * 0.5
                exp("Delivery Platform Commission", "Swiggy Commission", delivery_rev * 0.55 * 0.22,
                    note="Swiggy commission for previous month")
                exp("Delivery Platform Commission", "Zomato Commission", delivery_rev * 0.45 * 0.20,
                    note="Zomato commission for previous month")
            if day.day == 5:
                exp("Electricity & Utilities", "Electricity",
                    rng.uniform(14000, 19000) + (4000 if day.month in (4, 5, 6) else 0))
                exp("Electricity & Utilities", "Internet & Telephone", 1800)
                exp("Electricity & Utilities", "Water", 1500, PaymentMode.CASH)
                exp("Taxes", "Professional Tax", 1600)
            if day.day == 10 and month_sales.get(prev):
                exp("Taxes", "GST Payment", float(month_sales[prev]) * 0.05 * 0.85, note="Net GST for previous month")
            if day.day == 12 and month_sales.get(prev):
                exp("Bank & Payment Charges", None, float(month_sales[prev]) * 0.5 * 0.006, note="UPI / card MDR")
            if day.day == 3:
                exp("Marketing & Promotions", "Instagram & Facebook Ads", rng.uniform(6000, 12000), note="Social ads")
            if (day.month, day.day) in ((3, 25), (6, 5)):
                exp("Marketing & Promotions", "Festival Offers", rng.uniform(20000, 35000), note="Ramzan/Eid campaign")
            if (day.month, day.day) == (10, 20):
                exp("Marketing & Promotions", "Festival Offers", rng.uniform(25000, 40000), note="Diwali campaign")
                exp("Marketing & Promotions", "Flex & Hoardings", rng.uniform(8000, 15000), note="Festival banners")
            if (day.month, day.day) == (10, 30):
                exp("Staff Incentives & Bonus", "Festival Bonus", 45000, note="Diwali bonus")
                exp("Staff Welfare", "Festival Lunch", 7000, PaymentMode.CASH, note="Staff festival lunch")
            if (day.month, day.day) in ((3, 15), (6, 15), (9, 15), (12, 15)):
                exp("Taxes", "Income Tax Advance", rng.uniform(45000, 75000), note="Quarterly advance tax")

            # ---- Ad-hoc restaurant expenses ----
            for _ in range(rng.choice([0, 1, 1, 2])):
                cname, sname, lo, hi = rng.choice([
                    ("Staff Welfare", "Staff Meals", 400, 1200), ("Miscellaneous", None, 100, 2000),
                    ("Transport & Courier", None, 150, 1500), ("Shop Maintenance", "Cleaning Supplies", 200, 1000),
                    ("Shop Maintenance", "Kitchen Equipment", 500, 6000), ("Shop Maintenance", "Pest Control", 800, 2500),
                ])
                exp(cname, sname, rng.uniform(lo, hi), mode(0.2), note=rng.choice(["", "", "Urgent"]))

            # ---- Owner drawings, 5th of each month ----
            if day.day == 5 and day != START_DATE:
                partner_txns.append(PartnerTransaction(
                    partner=owner, date=day, kind="WITHDRAWAL",
                    amount=D(rng.choice([60000, 70000, 80000, 90000])), payment_mode=PaymentMode.BANK,
                    note="Owner drawing",
                ))
            day += datetime.timedelta(days=1)

        # --- A couple of outstanding supplier bills on credit ---
        today = datetime.date.today()
        for vendor, age, due_in, amt, paid, note in [
            ("Al Barkath Meat & Poultry Suppliers", 10, 5, 68000, 0, "Meat supply, credit bill"),
            ("Hyderabad Spice Wholesale Mart", 20, -5, 34000, 20000, "Spice stock bill overdue"),
        ]:
            payable = Payable(
                vendor=vendor, bill_date=min(today, end) - datetime.timedelta(days=age),
                due_date=min(today, end) + datetime.timedelta(days=due_in), amount=D(amt), amount_paid=D(paid),
                note=note,
            )
            payments = []
            if paid:
                payments.append(PurchaseEntry(
                    date=payable.bill_date + datetime.timedelta(days=5), vendor=vendor, amount=D(paid),
                    payment_mode=PaymentMode.BANK, note=f"Payment to {vendor} for bill",
                ))
            payables.append((payable, payments))

        # --- Cash & bank movement ----
        flows = {}

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
            if cash_bal < D("15000"):
                need = D(int(math.ceil(float(D("40000") - cash_bal) / 5000) * 5000))
                transfers.append(CashTransfer(date=d, direction=CashTransfer.Direction.BANK_TO_CASH, amount=need,
                                              note="Cash withdrawn for till"))
                cash_bal += need
                bank_bal -= need
            elif cash_bal > D("100000") and d.weekday() in (1, 4):
                dep = D(int((cash_bal - D("50000")) // 5000 * 5000))
                transfers.append(CashTransfer(date=d, direction=CashTransfer.Direction.CASH_TO_BANK, amount=dep,
                                              note="Cash deposit"))
                cash_bal -= dep
                bank_bal += dep
            if bank_bal < D("60000"):
                amt = D(int(math.ceil(float(D("250000") - bank_bal) / 25000) * 25000))
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
        biryani_units = sum(units_sold.get(n, 0) for n in BIRYANIS)
        days = (end - START_DATE).days + 1
        self.stdout.write(
            f"  {days} days ({START_DATE} to {end}): {len(sales)} sales lines, {len(purchases)} purchases "
            f"(+{len(payables)} credit bills), {len(expenses)} expenses, {len(transfers)} cash/bank transfers, "
            f"{len(partner_txns)} owner transactions ({topups} capital top-ups)."
        )
        self.stdout.write(f"  Total biryani plates sold: {biryani_units:,} (avg {biryani_units / days:.1f}/day)")
        for name in BIRYANIS:
            self.stdout.write(f"    {name}: {units_sold.get(name, 0):,}")
        self.stdout.write(
            f"  Revenue Rs.{revenue:,.0f} | raw material bought Rs.{stock:,.0f} | expenses Rs.{spend:,.0f} | "
            f"operating result Rs.{revenue - stock - spend:,.0f} | closing cash Rs.{cash_bal:,.0f} bank Rs.{bank_bal:,.0f}."
        )
