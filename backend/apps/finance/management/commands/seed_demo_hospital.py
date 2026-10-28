import datetime
import random
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.organizations.tenant_context import set_current_organization

from ...models import (
    BankAccount, CashTransfer, Category, ClaimPayment, Customer, ExpenseEntry, FinanceSettings,
    GeneralLedger, InsuranceClaim, KickbackEntry, LedgerEntry, Partner, PartnerTransaction, Payable,
    PaymentMode, PurchaseEntry, Receivable, SalesEntry, Subcategory, Vendor, sync_claim_totals,
)

DEMO_USERNAME = "demo_hospital"
# (department, doctor): departments are subcategories of OPD Consultation, doctors sit under them.
DOCTORS = [("Cardiology", "Dr. Rao"), ("Orthopaedics", "Dr. Sharma"), ("General Medicine", "Dr. Reddy"),
           ("Paediatrics", "Dr. Iyer"), ("Gynaecology", "Dr. Khan")]
REFERRERS = ["Ramesh RMP", "Suresh Clinic", "Lakshmi Medicals", "Venkat PHC", "Anand Diagnostics"]
# Top health insurers by FY25 health premium (New India, Star Health, then National, ICICI Lombard, United India).
INSURERS = ["New India Assurance", "Star Health", "National Insurance", "ICICI Lombard", "United India Insurance"]
TPAS = ["MediAssist", "Paramount TPA", "Vidal Health", "Family Health Plan"]
DIAGNOSES = ["Appendicitis", "Fracture - femur", "Acute MI", "Dengue fever", "Normal delivery", "C-section",
             "Pneumonia", "Cataract surgery", "Hernia repair", "Typhoid", "Gall stones", "Knee replacement"]
FIRST = ["Ravi", "Sita", "Arjun", "Meena", "Kiran", "Lata", "Mohan", "Priya", "Sunil", "Anita", "Rahul", "Geeta"]
LAST = ["Kumar", "Devi", "Reddy", "Naidu", "Rao", "Sharma", "Patel", "Singh", "Gupta", "Prasad"]
# Salaries & Wages > department > employee. Monthly pay range per department.
PAYROLL = {
    "Nursing": ["Lakshmi Bai", "Sunitha Rao", "Kavitha M", "Padma Devi", "Shabana Begum", "Rekha Naidu",
                "Anjali Verma", "Savitri K"],
    "Reception": ["Harika P", "Rama Krishna", "Suresh B", "Divya S"],
    "Backoffice": ["Mahesh Gowd", "Venkatesh T", "Naveen Kumar"],
    "Medical": ["Ravi Teja", "Pooja Sharma", "Imran Ali", "Sneha Kulkarni"],
}
PAY_RANGE = {"Nursing": (18000, 26000), "Reception": (13000, 17000), "Backoffice": (15000, 22000),
             "Medical": (22000, 35000)}


def money(n) -> Decimal:
    return Decimal(int(n))


def after(d, days, cap):
    return min(d + datetime.timedelta(days=days), cap)


class Command(BaseCommand):
    help = "Fill the demo_hospital organization with ~3 months of realistic hospital data (dev only)."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=3)
        parser.add_argument("--reset", action="store_true", help="Delete the org's existing finance data first.")

    @transaction.atomic
    def handle(self, *args, months, reset, **options):
        user = User.objects.filter(username=DEMO_USERNAME).select_related("organization").first()
        if user is None:
            raise CommandError(f"No user '{DEMO_USERNAME}'.")
        set_current_organization(user.organization)
        self.email = user.email
        if SalesEntry.objects.exists() and not reset:
            raise CommandError("Org already has data; pass --reset to wipe and reseed.")
        if reset:
            self._wipe()
        random.seed(2026)
        self.today = datetime.date.today()
        self.start = self.today - datetime.timedelta(days=30 * months)
        self._setup()
        self._daily()
        self._monthly()
        self._admissions()
        self._misc()
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {self.start} to {self.today}: {SalesEntry.objects.count()} sales, "
            f"{ExpenseEntry.objects.count()} expenses, {PurchaseEntry.objects.count()} purchases, "
            f"{KickbackEntry.objects.count()} admissions, {InsuranceClaim.objects.count()} claims."))

    def _wipe(self):
        for m in (ClaimPayment, InsuranceClaim, SalesEntry, ExpenseEntry, KickbackEntry, PurchaseEntry,
                  Receivable, Payable, CashTransfer, PartnerTransaction, Partner, LedgerEntry, GeneralLedger,
                  Customer, Vendor, BankAccount):
            for obj in list(m.objects.all()):
                obj.delete()
        # Old flat doctor subcategories; departments and doctors are recreated by _setup.
        Subcategory.objects.filter(category__kind=Category.Kind.SALES, category__name="OPD Consultation",
                                   parent=None).delete()
        Subcategory.objects.filter(category__kind=Category.Kind.EXPENSE,
                                   category__name__in=["Salaries & Wages", "Doctor Fees",
                                                       "Referral Commission"]).delete()

    def cat(self, kind, name, gst=0):
        c, _ = Category.objects.get_or_create(kind=kind, name=name, defaults={"gst_rate": gst})
        return c

    def _setup(self):
        S, E, P = Category.Kind.SALES, Category.Kind.EXPENSE, Category.Kind.PURCHASE
        self.opd = self.cat(S, "OPD Consultation")
        self.ip = self.cat(S, "IP Billing")
        self.pharm = self.cat(S, "Pharmacy", 5)
        self.lab = self.cat(S, "Laboratory")
        self.rad = self.cat(S, "Radiology")
        self.proc = self.cat(S, "Procedures")
        self.exp = {n: self.cat(E, n) for n in (
            "Salaries & Wages", "Rent", "Utilities", "Maintenance", "Marketing", "Miscellaneous",
            "Bank & Payment Charges", "Doctor Fees", "Referral Commission", "Housekeeping & Laundry")}
        self.pur = {n: self.cat(P, n) for n in ("Medicines", "Surgical Supplies", "Lab Reagents", "Equipment")}
        self.doctor_subs = []
        for dept, doctor in DOCTORS:
            department = Subcategory.objects.get_or_create(category=self.opd, parent=None, name=dept)[0]
            self.doctor_subs.append(
                Subcategory.objects.get_or_create(category=self.opd, parent=department, name=doctor)[0])
        # Salaries & Wages > department > employee, each with a fixed monthly pay.
        salaries = self.exp["Salaries & Wages"]
        self.payroll = []
        for dept, names in PAYROLL.items():
            department = Subcategory.objects.get_or_create(category=salaries, parent=None, name=dept)[0]
            for name in names:
                sub = Subcategory.objects.get_or_create(category=salaries, parent=department, name=name)[0]
                self.payroll.append((sub, random.randint(*PAY_RANGE[dept])))
        # Doctor Fees > doctor, each with a fixed monthly fee.
        self.doctor_fees = []
        for _, doctor in DOCTORS:
            sub = Subcategory.objects.get_or_create(category=self.exp["Doctor Fees"], parent=None, name=doctor)[0]
            self.doctor_fees.append((sub, random.randint(50000, 70000)))
        # Referral Commission > referrer, for admissions and monthly OPD/lab referral payouts.
        referral = self.exp["Referral Commission"]
        self.referrer_subs = {r: Subcategory.objects.get_or_create(category=referral, parent=None, name=r)[0]
                              for r in REFERRERS}
        fs = FinanceSettings.objects.first()
        fs.opening_date = self.start
        fs.opening_balance = money(150000)
        fs.opening_bank_balance = money(800000)
        fs.kickback_doctor_category = self.opd
        fs.ip_revenue_category = self.ip
        fs.save()
        self.banks = [
            BankAccount.objects.create(name="Current A/c - HDFC", bank_name="HDFC", account_number="XXXX4821",
                                       opening_balance=money(600000), opening_balance_as_on=self.start),
            BankAccount.objects.create(name="Collections A/c - SBI", bank_name="SBI", account_number="XXXX7710",
                                       opening_balance=money(200000), opening_balance_as_on=self.start),
        ]
        self.vendors = ["MediCare Distributors", "Apex Surgicals", "LabChem Supplies", "Sri Sai Pharma",
                        "BioTech Reagents"]
        for v in self.vendors:
            Vendor.objects.create(name=v, phone="9" + str(random.randint(100000000, 999999999)),
                                  opening_balance_as_on=self.start)
        self.customers = [Customer.objects.create(name=n) for n in (
            "Singareni Collieries Staff Scheme", "Municipal Corporation Employees", "Railway Health Unit",
            "Govt. Teachers Health Fund", "Aarogyasri Trust")]

    def person(self):
        return f"{random.choice(FIRST)} {random.choice(LAST)}"

    def sale(self, d, channel, amount, mode=None, sub=None, note="", **kw):
        mode = mode or (PaymentMode.CASH if random.random() < 0.55 else PaymentMode.BANK)
        return SalesEntry.objects.create(
            date=d, channel=channel, subcategory=sub, gross_amount=money(amount), amount=money(amount),
            payment_mode=mode, bank_account=random.choice(self.banks) if mode == PaymentMode.BANK else None,
            note=note, created_by_email=self.email, **kw)

    def expense(self, d, cat, amount, note="", mode=None, **kw):
        mode = mode or (PaymentMode.CASH if random.random() < 0.5 else PaymentMode.BANK)
        return ExpenseEntry.objects.create(
            date=d, category=cat, amount=money(amount), payment_mode=mode, note=note,
            bank_account=self.banks[0] if mode == PaymentMode.BANK else None,
            created_by_email=self.email, **kw)

    def _daily(self):
        purchase_ranges = {"Medicines": (40000, 120000), "Surgical Supplies": (20000, 70000),
                           "Lab Reagents": (10000, 35000), "Equipment": (30000, 150000)}
        d = self.start
        while d <= self.today:
            f = (1.0 if d.weekday() < 6 else 0.5) * (1 + (d - self.start).days / 400)  # Sunday quiet, slow growth
            for sub in self.doctor_subs:
                n = int(random.randint(6, 16) * f)
                if n:
                    self.sale(d, self.opd, n * random.choice([300, 400, 500]), sub=sub,
                              quantity=n, note=f"{n} OPD consultations")
            self.sale(d, self.pharm, random.randint(18000, 42000) * f, note="Pharmacy counter")
            self.sale(d, self.lab, random.randint(8000, 22000) * f, note="Lab tests")
            self.sale(d, self.rad, random.randint(5000, 18000) * f, note="X-ray / USG / CT")
            if random.random() < 0.45:
                self.sale(d, self.proc, random.randint(15000, 60000), note="Minor procedure / dressing")
            self.expense(d, self.exp["Miscellaneous"], random.randint(500, 3000), "Daily petty cash",
                         mode=PaymentMode.CASH)
            if random.random() < 0.3:
                self.expense(d, self.exp["Housekeeping & Laundry"], random.randint(1500, 4000),
                             "Laundry / cleaning supplies")
            if random.random() < 0.15:
                self.expense(d, self.exp["Maintenance"], random.randint(2000, 15000), "Equipment / plumbing repair")
            if d.weekday() in (0, 3) or random.random() < 0.2:
                name = random.choice(list(self.pur))
                PurchaseEntry.objects.create(
                    date=d, category=self.pur[name], vendor=random.choice(self.vendors),
                    quantity=random.randint(5, 80), amount=money(random.randint(*purchase_ranges[name])),
                    payment_mode=PaymentMode.BANK, bank_account=self.banks[0], note=f"{name} stock",
                    created_by_email=self.email)
            if d.weekday() == 5:
                CashTransfer.objects.create(
                    date=d, direction=CashTransfer.Direction.CASH_TO_BANK,
                    amount=money(random.randint(60000, 150000)), bank_account=self.banks[1],
                    note="Weekly cash deposit", created_by_email=self.email)
            d += datetime.timedelta(days=1)

    def _month_dates(self, day):
        y, m = self.start.year, self.start.month
        while True:
            d = datetime.date(y, m, day)
            if d > self.today:
                return
            if d >= self.start:
                yield d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    def _monthly(self):
        for d in self._month_dates(10):
            for name, sub in self.referrer_subs.items():
                self.expense(d, self.exp["Referral Commission"], money(random.randint(4000, 18000)),
                             f"OPD & lab referral payout · {name}", mode=PaymentMode.BANK, subcategory=sub)
        for d in self._month_dates(1):
            self.expense(d, self.exp["Rent"], 85000, "Building rent", mode=PaymentMode.BANK)
        for d in self._month_dates(5):
            self.expense(d, self.exp["Utilities"], random.randint(60000, 90000), "Electricity, water, oxygen",
                         mode=PaymentMode.BANK)
            self.expense(d, self.exp["Marketing"], random.randint(8000, 20000), "Health camps & local ads",
                         mode=PaymentMode.BANK)
        for d in self._month_dates(28):
            for sub, amount in self.payroll:
                self.expense(d, self.exp["Salaries & Wages"], money(amount), f"Salary · {sub.name}",
                             mode=PaymentMode.BANK, subcategory=sub)
            for sub, amount in self.doctor_fees:
                self.expense(d, self.exp["Doctor Fees"], money(amount), f"Consultant fee · {sub.name}",
                             mode=PaymentMode.BANK, subcategory=sub)
            self.expense(d, self.exp["Bank & Payment Charges"], random.randint(2500, 6000),
                         "Gateway & bank charges", mode=PaymentMode.BANK)

    def _admissions(self):
        span = (self.today - self.start).days - 3
        for i in range(48):
            adm = self.start + datetime.timedelta(days=random.randint(0, span))
            dis = after(adm, random.randint(2, 7), self.today)
            final = money(random.choice([25000, 38000, 52000, 75000, 110000, 160000, 240000])
                          * random.uniform(0.9, 1.15))
            insured = random.random() < 0.5
            discount = money(final * Decimal(str(random.choice([0, 0, 0.02, 0.05]))))
            insurer_part = money((final - discount) * Decimal("0.8")) if insured else Decimal(0)
            patient_part = final - discount - insurer_part
            refs = random.sample(REFERRERS, random.choice([0, 1, 1, 2]))
            doc = random.choice(self.doctor_subs)
            patient = self.person()
            insurer = random.choice(INSURERS) if insured else ""
            bill = KickbackEntry.objects.create(
                bill_number=f"IP-{adm:%y%m}-{i + 1:03d}", admission_date=adm, discharge_date=dis,
                consulting_doctor=doc.name, consulting_doctor_item=doc, referred_by=", ".join(refs),
                final_amount=final, patient_name=patient,
                patient_address=random.choice(["Hyderabad", "Warangal", "Karimnagar", "Nizamabad"]),
                payer_type="INSURANCE" if insured else "CASH", insurer_name=insurer,
                policy_number=f"POL{random.randint(100000, 999999)}" if insured else "",
                preauth_number=f"PA{random.randint(10000, 99999)}" if insured else "",
                patient_amount=patient_part, insurer_amount=insurer_part, discount_amount=discount,
                mrn=f"MRN{random.randint(10000, 99999)}",
                patient_phone="9" + str(random.randint(100000000, 999999999)),
                patient_age=random.randint(3, 80), patient_gender=random.choice(["M", "F"]),
                ward=f"{random.choice(['General', 'Semi-private', 'Private', 'ICU'])} / {random.randint(1, 40)}",
                diagnosis=random.choice(DIAGNOSES), created_by_email=self.email)
            if patient_part > 0:
                self.sale(dis, self.ip, patient_part, kickback_bill=bill,
                          note=f"Patient payment · {bill.bill_number} · {patient}")
            for r in refs:
                self.expense(after(dis, 2, self.today), self.exp["Referral Commission"],
                             money(final * Decimal("0.05") / len(refs)),
                             f"Referral commission · {r} · {bill.bill_number}", kickback_bill=bill,
                             subcategory=self.referrer_subs[r])
            if insured:
                self._claim(bill, patient, insurer, insurer_part, dis)

    def _claim(self, bill, patient, insurer, amount, dis):
        submitted = dis + datetime.timedelta(days=random.randint(1, 4))
        if submitted > self.today:
            InsuranceClaim.objects.create(
                bill=bill, claim_type="CLAIM", patient_name=patient, insurer_name=insurer, status="DRAFT",
                claimed_amount=amount, created_by_email=self.email)
            return
        outcome = random.choices(["settled", "partial", "approved", "query", "rejected", "submitted"],
                                 weights=[40, 15, 15, 10, 5, 15])[0]
        status = {"settled": "APPROVED", "partial": "APPROVED", "approved": "APPROVED", "query": "QUERY",
                  "rejected": "REJECTED", "submitted": "SUBMITTED"}[outcome]
        approved = (money(amount * Decimal(str(random.choice([1, 1, 0.95, 0.9]))))
                    if outcome in ("settled", "partial", "approved") else Decimal(0))
        decided = approved or outcome == "rejected"
        claim = InsuranceClaim.objects.create(
            bill=bill, claim_type="CLAIM", claim_number=f"CLM{random.randint(100000, 999999)}",
            patient_name=patient, insurer_name=insurer, tpa_name=random.choice(TPAS),
            policy_number=bill.policy_number, status=status, claimed_amount=amount, approved_amount=approved,
            submitted_date=submitted,
            decision_date=submitted + datetime.timedelta(days=random.randint(5, 15)) if decided else None,
            remarks={"query": "Awaiting discharge summary", "rejected": "Non-payable items"}.get(outcome, ""),
            created_by_email=self.email)
        if outcome in ("settled", "partial"):
            tds = money(approved * Decimal("0.01"))
            part = approved - tds if outcome == "settled" else money((approved - tds) * Decimal("0.6"))
            pdate = submitted + datetime.timedelta(days=random.randint(18, 40))
            if pdate <= self.today:
                entry = SalesEntry.objects.create(
                    date=pdate, amount=part, gross_amount=part, payment_mode=PaymentMode.BANK,
                    bank_account=self.banks[0], channel=self.ip, kickback_bill=bill,
                    created_by_email=self.email,
                    note=f"Insurance claim {claim.claim_number} · {claim.insurer_display}")
                ClaimPayment.objects.create(
                    claim=claim, date=pdate, amount=part, deduction=tds,
                    reference=f"UTR{random.randint(10**9, 10**10 - 1)}", sales_entry=entry)
                sync_claim_totals(claim.pk)

    def _misc(self):
        mid = self.start + datetime.timedelta(days=45)
        p = Partner.objects.create(name="Dr. Venkat Reddy (Promoter)", opening_balance_as_on=self.start)
        PartnerTransaction.objects.create(
            partner=p, date=self.start + datetime.timedelta(days=10), kind="INVESTMENT", amount=money(500000),
            payment_mode=PaymentMode.BANK, bank_account=self.banks[0], note="Capital for new ICU beds",
            created_by_email=self.email)
        PartnerTransaction.objects.create(
            partner=p, date=self.today - datetime.timedelta(days=12), kind="WITHDRAWAL", amount=money(150000),
            payment_mode=PaymentMode.BANK, bank_account=self.banks[0], note="Drawings",
            created_by_email=self.email)
        for cust in self.customers[:4]:
            inv = mid + datetime.timedelta(days=random.randint(0, 30))
            amt = money(random.randint(60000, 220000))
            Receivable.objects.create(
                customer=cust, invoice_date=inv, due_date=inv + datetime.timedelta(days=30), amount=amt,
                amount_received=random.choice([0, 0, amt // 2, amt]), note="Monthly corporate treatment billing",
                created_by_email=self.email)
        for v in self.vendors[:4]:
            bd = mid + datetime.timedelta(days=random.randint(0, 35))
            amt = money(random.randint(40000, 180000))
            Payable.objects.create(
                vendor=v, bill_date=bd, due_date=bd + datetime.timedelta(days=30), amount=amt,
                amount_paid=random.choice([0, amt // 2, amt]), note="Supplier credit bill",
                created_by_email=self.email)
        led = GeneralLedger.objects.create(name="Hospital Building Loan", opening_balance=money(2500000),
                                           opening_date=self.start, profession="Bank loan", state="Telangana")
        for d in self._month_dates(10):
            LedgerEntry.objects.create(ledger=led, date=d, particular="EMI paid", credit=money(65000))
