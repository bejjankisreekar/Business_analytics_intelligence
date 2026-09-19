import datetime
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

ORG_NAME = "Apollo Hospitals"
ADMIN_EMAIL = "admin@apollohospitals.com"
ADMIN_USERNAME = "apollo_admin"
ADMIN_PASSWORD = "ApolloHosp#2025"

# A rolling year ending today, so the demo data always covers the last 12
# months however long after it was written the seed is run.
START_DATE = datetime.date.today() - datetime.timedelta(days=365)

D = Decimal

# --------------------------------------------------------------------------
# Revenue: category -> (gst %, {sub-category: [children] or None})
# A child is a named person/item under a sub-category, e.g. a doctor under a
# specialty.
# --------------------------------------------------------------------------
SALES = {
    "OPD Consultation": (0, {
        "Cardiology": ["Dr. Rao", "Dr. Mehta", "Dr. Iyer"],
        "Orthopedics": ["Dr. Khan", "Dr. Nair"],
        "General Medicine": ["Dr. Sharma", "Dr. Reddy"],
        "Pediatrics": ["Dr. Patel", "Dr. Menon"],
        "Gynecology": ["Dr. Kulkarni", "Dr. Bose"],
        "Dermatology": ["Dr. Singh"],
        "ENT": ["Dr. Thomas"],
        "Neurology": ["Dr. Banerjee", "Dr. Kapoor"],
    }),
    "IPD Admission": (0, {
        "General Ward": None, "Semi-Private Room": None, "Private Room": None, "ICU": None, "Day Care": None,
    }),
    "Pharmacy Sales": (12, {
        "OTC Medicines": None, "Prescription Medicines": None, "Surgical Consumables": None, "Nutrition & Supplements": None,
    }),
    "Diagnostic Lab (Pathology)": (0, {
        "Blood Tests": None, "Urine Tests": None, "Biopsy & Histopathology": None, "Microbiology": None,
    }),
    "Radiology & Imaging": (0, {
        "X-Ray": None, "CT Scan": None, "MRI": None, "Ultrasound": None, "Mammography": None,
    }),
    "Surgery & Operation Theatre": (0, {
        "General Surgery": ["Dr. Verma", "Dr. Joshi"],
        "Orthopedic Surgery": ["Dr. Khan", "Dr. Nair"],
        "Cardiac Surgery": ["Dr. Iyer", "Dr. Rao"],
        "Neurosurgery": ["Dr. Fernandes"],
    }),
    "Ambulance Services": (0, {"Basic Life Support": None, "Advanced Life Support": None}),
    "Health Checkup Packages": (0, {
        "Basic Checkup": None, "Executive Checkup": None, "Senior Citizen Package": None, "Cardiac Screening": None,
    }),
    "Physiotherapy & Rehab": (0, {"Sports Rehab": None, "Post-Op Rehab": None, "Neuro Rehab": None}),
    "Insurance / TPA Claims": (0, {
        "Star Health": None, "ICICI Lombard": None, "HDFC Ergo": None, "Niva Bupa": None, "Care Health": None,
    }),
}

# --------------------------------------------------------------------------
# Expenses. "Salaries & Wages" is department -> named employee, with a
# monthly salary each; the rest are ordinary categories with sub-categories.
# --------------------------------------------------------------------------
SALARIES = {
    "Nursing": [("Sr. Anita Joseph", 52000), ("Sr. Mary Thomas", 48000), ("Sr. Kavya Nair", 46000),
                ("Sr. Rekha Das", 44000), ("Sr. Bindu Varghese", 42000)],
    "Administration": [("Rajesh Kumar", 68000), ("Sunita Verma", 56000), ("Vikram Rao", 38000)],
    "Pharmacy": [("Pooja Shah", 45000), ("Imran Ali", 39000)],
    "Laboratory": [("Sanjay Gupta", 47000), ("Neha Kapoor", 44000), ("Faisal Khan", 36000)],
    "Radiology": [("Arjun Pillai", 58000), ("Deepa Menon", 52000)],
    "Housekeeping": [("Lakshmi Bai", 19000), ("Ramu Yadav", 18000), ("Suresh Kumar", 18000)],
    "Security": [("Mohan Singh", 21000), ("Ravi Teja", 20000)],
    "IT & Systems": [("Karthik Reddy", 62000)],
}

DOCTOR_PAYOUTS = {
    "Visiting Consultants": [("Dr. Anand Bhatt", 180000), ("Dr. Shalini Rao", 150000)],
    "Resident Doctors": [("Dr. Kiran Kumar", 95000), ("Dr. Sana Sheikh", 92000), ("Dr. Rohit Jain", 90000)],
    "Surgeons": [("Dr. Verma", 240000), ("Dr. Joshi", 210000)],
}

EXPENSES = {
    "Medical Supplies & Consumables": ["Syringes & Needles", "Dressings & Bandages", "Disinfectants"],
    "Biomedical Waste Disposal": [],
    "Equipment Maintenance & AMC": ["MRI AMC", "CT Scanner AMC", "Lift & Generator AMC", "Ventilator Service"],
    "Housekeeping & Sanitation": [],
    "Electricity & Utilities": ["Electricity", "Water", "Diesel for Generator", "Internet & Telephone"],
    "Rent": [],
    "Insurance Premium": ["Professional Indemnity", "Fire & Property"],
    "Ambulance Fuel & Maintenance": ["Fuel", "Servicing & Repairs"],
    "Marketing & Patient Outreach": ["Health Camps", "Digital Advertising", "Print & Hoardings"],
    "IT & Software (HMS)": [],
    "Bank & Payment Charges": [],
    "Taxes": ["GST Payment", "TDS", "Professional Tax"],
    "Staff Welfare & Training": ["Uniforms", "CME Programs", "Canteen"],
    "Miscellaneous": [],
}
INACTIVE_EXPENSE_CATEGORY = "Legacy Consultancy (discontinued)"

PURCHASES = {
    "Pharmaceuticals & Medicines": (12, ["Antibiotics", "Analgesics", "Cardiac Drugs", "IV Fluids", "Diabetic Care"]),
    "Surgical Instruments & Consumables": (12, ["Sutures", "Gloves & Masks", "Implants & Stents", "Surgical Blades"]),
    "Diagnostic Reagents & Lab Kits": (12, ["Hematology Reagents", "Biochemistry Kits", "Serology Kits"]),
    "Medical Equipment": (18, ["Patient Monitors", "Ventilators", "Imaging Equipment", "Infusion Pumps"]),
    "PPE & Safety Supplies": (5, []),
    "Hospital Furniture & Fixtures": (18, ["Beds & Trolleys", "OT Furniture"]),
    "Office & IT Supplies": (18, ["Computers & Printers", "Stationery"]),
    "Linen & Housekeeping Supplies": (5, []),
}

VENDORS = [
    ("MedPlus Pharma Distributors", "9876500001", "Bulk medicine supplier"),
    ("Apex Surgical Supplies", "9876500002", "Surgical instruments & consumables"),
    ("Siemens Healthineers", "9876500003", "Diagnostic & imaging equipment"),
    ("LabCorp Reagents India", "9876500004", "Lab reagents & kits"),
    ("Trident Linen Supplies", "9876500005", "Linen & housekeeping"),
    ("Sun Pharma Wholesale", "9876500006", "Generic & branded medicines"),
    ("Philips Medical Systems", "9876500007", "Monitors & ventilators"),
    ("Godrej Interio", "9876500008", "Hospital furniture"),
    ("3M Healthcare India", "9876500009", "PPE & consumables"),
]
PURCHASE_VENDOR = {
    "Pharmaceuticals & Medicines": ["MedPlus Pharma Distributors", "Sun Pharma Wholesale"],
    "Surgical Instruments & Consumables": ["Apex Surgical Supplies", "3M Healthcare India"],
    "Diagnostic Reagents & Lab Kits": ["LabCorp Reagents India"],
    "Medical Equipment": ["Siemens Healthineers", "Philips Medical Systems"],
    "PPE & Safety Supplies": ["3M Healthcare India"],
    "Hospital Furniture & Fixtures": ["Godrej Interio"],
    "Office & IT Supplies": ["Apex Surgical Supplies"],
    "Linen & Housekeeping Supplies": ["Trident Linen Supplies"],
}

PATIENTS = [
    ("Ramesh Kumar", "9000011111"), ("Lakshmi Narayana", "9000022222"), ("Fatima Sheikh", "9000033333"),
    ("Arjun Verma", "9000044444"), ("Priya Menon", "9000055555"), ("Suresh Babu", "9000066666"),
    ("Anjali Deshmukh", "9000077777"), ("Mohammed Rafi", "9000088888"), ("Kavitha Rani", "9000099999"),
    ("Gopal Krishna", "9000010101"), ("Shobha Rao", "9000020202"), ("Vijay Anand", "9000030303"),
]
TPAS = {
    "Star Health": "18002222222", "ICICI Lombard": "18001111111", "HDFC Ergo": "18003333333",
    "Niva Bupa": "18004444444", "Care Health": "18005555555",
}
INACTIVE_CUSTOMER = ("Old Corporate Account (closed)", "9000000000")
INACTIVE_VENDOR = ("Discontinued Supplier Co", "9876599999", "No longer used")

# Approximate ticket size per revenue category (before growth).
TICKET = {
    "OPD Consultation": (600, 2200), "IPD Admission": (20000, 150000), "Pharmacy Sales": (800, 9000),
    "Diagnostic Lab (Pathology)": (500, 6500), "Radiology & Imaging": (900, 18000),
    "Surgery & Operation Theatre": (40000, 280000), "Ambulance Services": (800, 4500),
    "Health Checkup Packages": (1800, 12000), "Physiotherapy & Rehab": (500, 2500),
    "Insurance / TPA Claims": (25000, 120000),
}
# Relative daily frequency of each revenue stream.
WEIGHT = {
    "OPD Consultation": 30, "IPD Admission": 3, "Pharmacy Sales": 20, "Diagnostic Lab (Pathology)": 12,
    "Radiology & Imaging": 7, "Surgery & Operation Theatre": 2, "Ambulance Services": 3,
    "Health Checkup Packages": 5, "Physiotherapy & Rehab": 4, "Insurance / TPA Claims": 2,
}
# Streams where a quantity (visits, units, tests) is naturally recorded.
QUANTITY = {"OPD Consultation": (1, 6), "Pharmacy Sales": (1, 14), "Diagnostic Lab (Pathology)": (1, 8),
            "Radiology & Imaging": (1, 3), "Physiotherapy & Rehab": (1, 5), "Health Checkup Packages": (1, 4)}
NOTES = ["", "", "", "", "Follow-up", "Walk-in", "Referred", "Emergency", "Insurance patient", "Corporate tie-up"]


class Command(BaseCommand):
    help = (
        "Drops the 'Apollo Hospitals' organization and its tenant schema if present, then signs it up "
        "fresh and backfills a rolling year of varied hospital data (departments, staff, doctors, "
        "vendors, receivables, payables, partners)."
    )

    def handle(self, *args, **options):
        existing = Organization.objects.filter(name=ORG_NAME).first()
        if existing is not None:
            self.stdout.write(self.style.WARNING(
                f"Dropping '{ORG_NAME}' ({existing.organization_code}) and schema {existing.schema_name}..."
            ))
            delete_organization_and_tenant(existing)
        # delete_organization_and_tenant only deactivates users, which would clash with the new admin.
        User.objects.filter(username=ADMIN_USERNAME).delete()
        User.objects.filter(email=ADMIN_EMAIL).delete()

        org, _admin = create_organization_with_tenant_schema_and_admin(
            org_data={
                "name": ORG_NAME,
                "business_type": Organization.BusinessType.HEALTHCARE,
                "size": Organization.OrganizationSize.LARGE,
                "contact_person": "Suresh Reddy",
                "contact_email": ADMIN_EMAIL,
                "city": "Hyderabad",
                "state": "Telangana",
                "country": "India",
            },
            admin_data={
                "email": ADMIN_EMAIL,
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
                "first_name": "Suresh",
                "last_name": "Reddy",
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
        self.stdout.write(f"  Username: {ADMIN_USERNAME}  (or email {ADMIN_EMAIL})")
        self.stdout.write(f"  Password: {ADMIN_PASSWORD}")

    # ------------------------------------------------------------------
    def _seed(self):
        from apps.finance.models import (
            CashTransfer, Category, Customer, ExpenseEntry, FinanceSettings, Partner,
            PartnerTransaction, Payable, PaymentMode, PurchaseEntry, Receivable, SalesEntry,
            Subcategory, Vendor,
        )

        today = datetime.date.today()
        rng = random.Random(2025)

        def money(lo, hi):
            return D(str(round(rng.uniform(lo, hi), 2)))

        def mode(bank_bias=0.5):
            return PaymentMode.BANK if rng.random() < bank_bias else PaymentMode.CASH

        # --- Opening position -------------------------------------------------
        fs = FinanceSettings.objects.first()
        values = dict(fy_start_month=4, opening_balance=D("250000"), opening_bank_balance=D("1800000"),
                      opening_date=START_DATE)
        if fs is None:
            FinanceSettings.objects.create(**values)
        else:
            for k, v in values.items():
                setattr(fs, k, v)
            fs.save()

        # --- Categories & sub-categories (a fresh schema still has generic starters) ---
        Category.objects.all().delete()

        sales_cat, sales_sub = {}, {}   # name -> Category ; (cat, sub) -> Subcategory
        sales_child = {}                # (cat, sub) -> [child Subcategory]
        for cname, (gst, subs) in SALES.items():
            cat = Category.objects.create(kind=Category.Kind.SALES, name=cname, gst_rate=gst)
            sales_cat[cname] = cat
            for sname, children in subs.items():
                sub = Subcategory.objects.create(category=cat, name=sname)
                sales_sub[(cname, sname)] = sub
                if children:
                    sales_child[(cname, sname)] = [
                        Subcategory.objects.create(category=cat, parent=sub, name=n) for n in children
                    ]
        # A paused sub-category, to exercise the inactive path.
        Subcategory.objects.create(category=sales_cat["OPD Consultation"], name="Homeopathy (paused)", is_active=False)

        exp_cat = {}
        exp_sub = {}
        salary_cat = Category.objects.create(kind=Category.Kind.EXPENSE, name="Salaries & Wages")
        exp_cat["Salaries & Wages"] = salary_cat
        salary_emp = {}     # (dept, name) -> child Subcategory
        for dept, staff in SALARIES.items():
            dsub = Subcategory.objects.create(category=salary_cat, name=dept)
            exp_sub[("Salaries & Wages", dept)] = dsub
            for name, _ in staff:
                salary_emp[(dept, name)] = Subcategory.objects.create(category=salary_cat, parent=dsub, name=name)

        payout_cat = Category.objects.create(kind=Category.Kind.EXPENSE, name="Doctor Fees & Consultant Payouts")
        exp_cat["Doctor Fees & Consultant Payouts"] = payout_cat
        payout_emp = {}
        for group, docs in DOCTOR_PAYOUTS.items():
            gsub = Subcategory.objects.create(category=payout_cat, name=group)
            for name, _ in docs:
                payout_emp[(group, name)] = Subcategory.objects.create(category=payout_cat, parent=gsub, name=name)

        for cname, subs in EXPENSES.items():
            cat = Category.objects.create(kind=Category.Kind.EXPENSE, name=cname)
            exp_cat[cname] = cat
            for sname in subs:
                exp_sub[(cname, sname)] = Subcategory.objects.create(category=cat, name=sname)
        Category.objects.create(kind=Category.Kind.EXPENSE, name=INACTIVE_EXPENSE_CATEGORY, is_active=False)

        pur_cat, pur_sub = {}, {}
        for cname, (gst, subs) in PURCHASES.items():
            cat = Category.objects.create(kind=Category.Kind.PURCHASE, name=cname, gst_rate=gst)
            pur_cat[cname] = cat
            for sname in subs:
                pur_sub[(cname, sname)] = Subcategory.objects.create(category=cat, name=sname)

        # --- Directory: vendors, patients/TPAs, partners ----------------------
        for name, phone, details in VENDORS:
            Vendor.objects.create(
                name=name, phone=phone, details=details,
                opening_balance=D(rng.choice([0, 15000, 32000, 48000])), opening_balance_as_on=START_DATE,
            )
        Vendor.objects.create(
            name=INACTIVE_VENDOR[0], phone=INACTIVE_VENDOR[1], details=INACTIVE_VENDOR[2],
            opening_balance=D("0"), opening_balance_as_on=START_DATE, is_active=False,
        )

        patients = [Customer.objects.create(name=n, phone=p) for n, p in PATIENTS]
        tpa = {n: Customer.objects.create(name=f"{n} TPA", phone=p) for n, p in TPAS.items()}
        Customer.objects.create(name=INACTIVE_CUSTOMER[0], phone=INACTIVE_CUSTOMER[1], is_active=False)

        partners = {}
        for name, phone, amt in [("Dr. Suresh Reddy", "9999900001", 5000000), ("Dr. Kavita Rao", "9999900002", 3000000),
                                 ("Dr. Anil Kumar", "9999900003", 2000000)]:
            partners[name] = Partner.objects.create(
                name=name, phone=phone, opening_balance=D(amt), opening_balance_as_on=START_DATE,
            )

        # --- Daily operational entries ---------------------------------------
        sales_rows, expense_rows, purchase_rows, transfer_rows = [], [], [], []
        payables = []       # (Payable, [payment PurchaseEntry rows])
        cats_weighted = [c for c, w in WEIGHT.items() for _ in range(w)]

        def pick_sub(cname):
            """The child (e.g. a doctor) when the sub-category has children, else the sub-category."""
            subs = [k for k in sales_sub if k[0] == cname]
            if not subs:
                return None
            key = rng.choice(subs)
            kids = sales_child.get(key)
            return rng.choice(kids) if kids else sales_sub[key]

        month_done = set()
        days_since_deposit = 0
        day = START_DATE

        while day <= today:
            months_elapsed = (day.year - START_DATE.year) * 12 + (day.month - START_DATE.month)
            growth = 1 + months_elapsed * 0.015
            weekend = day.weekday() >= 5
            season = 1.15 if day.month in (6, 7, 8, 9) else 1.0       # monsoon: more OPD / pharmacy

            # ---- Revenue: many small OPD/pharmacy/lab lines, fewer big IPD/surgery ones ----
            for _ in range(rng.choice([6, 7, 8, 9, 10, 12])):
                cname = rng.choice(cats_weighted)
                lo, hi = TICKET[cname]
                base = rng.uniform(lo, hi) * growth * (season if cname in ("OPD Consultation", "Pharmacy Sales") else 1.0)
                if weekend and cname in ("OPD Consultation", "Physiotherapy & Rehab", "Health Checkup Packages"):
                    base *= 0.55
                qty = None
                if cname in QUANTITY and rng.random() < 0.7:
                    qty = rng.randint(*QUANTITY[cname])
                    base *= 0.35 + 0.15 * qty          # more units, larger bill
                if cname == "Insurance / TPA Claims":
                    sub = rng.choice([s for k, s in sales_sub.items() if k[0] == cname])
                    customer = tpa[sub.name]
                elif cname in ("OPD Consultation", "IPD Admission", "Surgery & Operation Theatre", "Diagnostic Lab (Pathology)"):
                    sub = pick_sub(cname)
                    customer = rng.choice(patients) if rng.random() < 0.4 else None
                else:
                    sub = pick_sub(cname)
                    customer = rng.choice(patients) if rng.random() < 0.15 else None
                bank = 0.92 if cname in ("IPD Admission", "Surgery & Operation Theatre", "Insurance / TPA Claims") else 0.5
                sales_rows.append(SalesEntry(
                    date=day, channel=sales_cat[cname], subcategory=sub, customer=customer, quantity=qty,
                    amount=D(str(round(base, 2))), payment_mode=mode(bank), note=rng.choice(NOTES),
                ))

            # ---- Monthly payroll & fixed costs, booked on the 1st ----
            if day.day == 1 and (day.year, day.month) not in month_done:
                month_done.add((day.year, day.month))
                for dept, staff in SALARIES.items():
                    for name, sal in staff:
                        expense_rows.append(ExpenseEntry(
                            date=day, category=salary_cat, subcategory=salary_emp[(dept, name)],
                            amount=D(str(round(sal * (1 + months_elapsed * 0.004), 2))),
                            payment_mode=PaymentMode.BANK, note=f"{dept} salary",
                        ))
                for group, docs in DOCTOR_PAYOUTS.items():
                    for name, fee in docs:
                        expense_rows.append(ExpenseEntry(
                            date=day, category=payout_cat, subcategory=payout_emp[(group, name)],
                            amount=D(str(round(fee * growth, 2))), payment_mode=PaymentMode.BANK, note="Monthly payout",
                        ))
                for cname, sname, amt in [
                    ("Rent", None, 150000), ("IT & Software (HMS)", None, 18000),
                    ("Electricity & Utilities", "Electricity", 125000), ("Electricity & Utilities", "Internet & Telephone", 9000),
                    ("Equipment Maintenance & AMC", "MRI AMC", 42000), ("Equipment Maintenance & AMC", "CT Scanner AMC", 36000),
                    ("Housekeeping & Sanitation", None, 32000), ("Biomedical Waste Disposal", None, 14000),
                ]:
                    expense_rows.append(ExpenseEntry(
                        date=day, category=exp_cat[cname], subcategory=exp_sub.get((cname, sname)),
                        amount=D(str(round(amt * growth, 2))), payment_mode=PaymentMode.BANK, note="",
                    ))
                if day.month in (4, 7, 10, 1):
                    expense_rows.append(ExpenseEntry(
                        date=day, category=exp_cat["Insurance Premium"],
                        subcategory=exp_sub[("Insurance Premium", rng.choice(["Professional Indemnity", "Fire & Property"]))],
                        amount=D(str(round(65000 * growth, 2))), payment_mode=PaymentMode.BANK, note="Quarterly premium",
                    ))
                    expense_rows.append(ExpenseEntry(
                        date=day, category=exp_cat["Taxes"], subcategory=exp_sub[("Taxes", "GST Payment")],
                        amount=money(180000, 320000), payment_mode=PaymentMode.BANK, note="Quarterly GST",
                    ))
                expense_rows.append(ExpenseEntry(
                    date=day, category=exp_cat["Taxes"], subcategory=exp_sub[("Taxes", "TDS")],
                    amount=money(60000, 110000), payment_mode=PaymentMode.BANK, note="Monthly TDS",
                ))
                expense_rows.append(ExpenseEntry(
                    date=day, category=exp_cat["Taxes"], subcategory=exp_sub[("Taxes", "Professional Tax")],
                    amount=money(18000, 26000), payment_mode=PaymentMode.BANK, note="",
                ))

            # ---- Ad-hoc expenses ----
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                cname = rng.choice([
                    "Medical Supplies & Consumables", "Biomedical Waste Disposal", "Equipment Maintenance & AMC",
                    "Housekeeping & Sanitation", "Ambulance Fuel & Maintenance", "Marketing & Patient Outreach",
                    "Bank & Payment Charges", "Miscellaneous", "Staff Welfare & Training", "Electricity & Utilities",
                ])
                subs = EXPENSES[cname]
                sname = rng.choice(subs) if subs else None
                expense_rows.append(ExpenseEntry(
                    date=day, category=exp_cat[cname], subcategory=exp_sub.get((cname, sname)),
                    amount=D(str(round(rng.uniform(800, 14000) * growth, 2))),
                    payment_mode=PaymentMode.BANK if cname == "Bank & Payment Charges" else mode(),
                    note=rng.choice(["", "", "Urgent", "Monthly top-up"]),
                ))

            # ---- Purchases / restocking; some bought on credit ----
            if rng.random() < 0.55:
                cname = rng.choice(list(PURCHASES))
                _, subs = PURCHASES[cname]
                sname = rng.choice(subs) if subs else None
                vendor_name = rng.choice(PURCHASE_VENDOR[cname])
                span = {"Pharmaceuticals & Medicines": (30000, 150000), "Surgical Instruments & Consumables": (10000, 60000),
                        "Diagnostic Reagents & Lab Kits": (8000, 45000), "Medical Equipment": (100000, 900000),
                        "PPE & Safety Supplies": (3000, 15000), "Hospital Furniture & Fixtures": (15000, 80000),
                        "Office & IT Supplies": (2000, 12000), "Linen & Housekeeping Supplies": (4000, 18000)}[cname]
                amt = D(str(round(rng.uniform(*span) * growth, 2)))
                qty = rng.choice([None, rng.randint(2, 60)])
                if rng.random() < 0.12:
                    # On credit: becomes a Payable, and is often (part-)paid off later, which books the purchase.
                    payable = Payable(
                        vendor=vendor_name, bill_date=day, due_date=day + datetime.timedelta(days=30), amount=amt,
                        note=" · ".join(b for b in [cname, sname, f"Qty {qty}" if qty else None] if b),
                    )
                    payments = []
                    roll = rng.random()
                    if roll < 0.55 and day + datetime.timedelta(days=20) <= today:
                        paid = amt if roll < 0.35 else (amt * D("0.5")).quantize(D("0.01"))
                        payments.append(PurchaseEntry(
                            date=day + datetime.timedelta(days=rng.randint(8, 24)), vendor=vendor_name, amount=paid,
                            payment_mode=PaymentMode.BANK, note=f"Payment to {vendor_name} for bill",
                        ))
                        payable.amount_paid = paid
                    payables.append((payable, payments))
                else:
                    purchase_rows.append(PurchaseEntry(
                        date=day, category=pur_cat[cname], subcategory=pur_sub.get((cname, sname)), vendor=vendor_name,
                        quantity=qty, amount=amt, payment_mode=mode(0.6), note=rng.choice(["", "", "Bulk order", "Reorder"]),
                    ))

            # ---- Weekly cash deposit; occasional withdrawal for petty cash ----
            days_since_deposit += 1
            if days_since_deposit >= 7 and not weekend:
                transfer_rows.append(CashTransfer(
                    date=day, direction=CashTransfer.Direction.CASH_TO_BANK,
                    amount=D(str(round(rng.uniform(25000, 70000) * growth, 2))), note="Weekly cash deposit",
                ))
                days_since_deposit = 0
            if day.day == 15 and rng.random() < 0.7:
                transfer_rows.append(CashTransfer(
                    date=day, direction=CashTransfer.Direction.BANK_TO_CASH,
                    amount=money(15000, 40000), note="Petty cash withdrawal",
                ))

            day += datetime.timedelta(days=1)

        # --- Receivables: claims in every state (untouched, part-paid, settled, overdue) ---
        receivable_specs = [
            ("Star Health", 20, 10, 185000, 60000, "Cashless claim settlement pending"),
            ("ICICI Lombard", 45, -15, 320000, 0, "Surgery claim overdue"),
            ("HDFC Ergo", 12, 18, 96000, 0, "Fresh claim, not yet paid"),
            ("Niva Bupa", 70, -40, 240000, 240000, "Fully settled claim"),
            ("Care Health", 33, -3, 152000, 100000, "Part-settled, balance overdue"),
        ]
        payments_sales = []
        for name, age, due_in, amt, received, note in receivable_specs:
            r = Receivable.objects.create(
                customer=tpa[name], invoice_date=today - datetime.timedelta(days=age),
                due_date=today + datetime.timedelta(days=due_in), amount=D(amt), amount_received=D(received), note=note,
            )
            if received:
                payments_sales.append(SalesEntry(
                    date=r.invoice_date + datetime.timedelta(days=rng.randint(6, 20)), customer=tpa[name],
                    amount=D(received), payment_mode=PaymentMode.BANK,
                    note=f"Payment received for invoice — {tpa[name].name}",
                ))
        for pat, age, due_in, amt, received, note in [
            (patients[0], 5, 10, 14500, 5000, "Full body checkup balance"),
            (patients[3], 18, -2, 38000, 0, "Post-op physiotherapy package, unpaid"),
        ]:
            r = Receivable.objects.create(
                customer=pat, invoice_date=today - datetime.timedelta(days=age),
                due_date=today + datetime.timedelta(days=due_in), amount=D(amt), amount_received=D(received), note=note,
            )
            if received:
                payments_sales.append(SalesEntry(
                    date=r.invoice_date + datetime.timedelta(days=2), customer=pat, amount=D(received),
                    payment_mode=PaymentMode.CASH, note=f"Payment received for invoice — {pat.name}",
                ))

        # --- Extra payables beyond those raised by on-credit purchases ---
        for vendor_name, age, due_in, amt, paid, note in [
            ("MedPlus Pharma Distributors", 25, -5, 245000, 100000, "Monthly medicine stock bill overdue"),
            ("Siemens Healthineers", 60, 15, 680000, 340000, "MRI machine AMC + parts, installment plan"),
            ("Philips Medical Systems", 8, 22, 415000, 0, "Ventilator lease, first installment"),
        ]:
            payable = Payable(
                vendor=vendor_name, bill_date=today - datetime.timedelta(days=age),
                due_date=today + datetime.timedelta(days=due_in), amount=D(amt), amount_paid=D(paid), note=note,
            )
            payments = []
            if paid:
                payments.append(PurchaseEntry(
                    date=payable.bill_date + datetime.timedelta(days=5), vendor=vendor_name, amount=D(paid),
                    payment_mode=PaymentMode.BANK, note=f"Payment to {vendor_name} for bill",
                ))
            payables.append((payable, payments))

        # --- Partner capital: investments and withdrawals through the year, cash and bank ---
        p_txns = []
        for who, offset, kind, amt, pmode, note in [
            ("Dr. Suresh Reddy", 0, "INVESTMENT", 500000, PaymentMode.BANK, "Additional capital infusion"),
            ("Dr. Suresh Reddy", 120, "WITHDRAWAL", 150000, PaymentMode.BANK, "Personal drawing"),
            ("Dr. Suresh Reddy", 260, "INVESTMENT", 300000, PaymentMode.BANK, "Equipment fund contribution"),
            ("Dr. Kavita Rao", 90, "WITHDRAWAL", 100000, PaymentMode.BANK, "Partner drawing"),
            ("Dr. Kavita Rao", 200, "INVESTMENT", 250000, PaymentMode.CASH, "Cash capital top-up"),
            ("Dr. Kavita Rao", 310, "WITHDRAWAL", 60000, PaymentMode.CASH, "Festival advance"),
            ("Dr. Anil Kumar", 30, "INVESTMENT", 400000, PaymentMode.BANK, "New wing funding"),
            ("Dr. Anil Kumar", 180, "WITHDRAWAL", 90000, PaymentMode.BANK, "Partner drawing"),
        ]:
            p_txns.append(PartnerTransaction(
                partner=partners[who], date=START_DATE + datetime.timedelta(days=offset), kind=kind,
                amount=D(amt), payment_mode=pmode, note=note,
            ))

        SalesEntry.objects.bulk_create(sales_rows + payments_sales, batch_size=500)
        ExpenseEntry.objects.bulk_create(expense_rows, batch_size=500)
        PurchaseEntry.objects.bulk_create(purchase_rows, batch_size=500)
        CashTransfer.objects.bulk_create(transfer_rows, batch_size=500)
        PartnerTransaction.objects.bulk_create(p_txns)
        for payable, payments in payables:
            payable.save()
            PurchaseEntry.objects.bulk_create(payments)

        self.stdout.write(
            f"  {len(sales_rows) + len(payments_sales)} sales, {len(expense_rows)} expenses, "
            f"{len(purchase_rows)} purchases (+{len(payables)} bills, {Receivable.objects.count()} invoices), "
            f"{len(transfer_rows)} transfers, {len(p_txns)} partner transactions "
            f"from {START_DATE} to {today}."
        )
