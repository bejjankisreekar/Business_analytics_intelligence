import datetime
import random
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import Coupon, CouponRedemption, Invoice, Payment, Plan, Subscription
from apps.organizations.models import Organization

# Every sample organization carries this slug prefix, which is how --delete
# finds them. Deleting an organization cascades to its subscriptions,
# invoices and payments, so nothing else needs cleaning up.
PREFIX = "sample-"
ALIAS = "dev"
COUPON_CODE = "SAMPLE-WELCOME"
BUSINESS_TYPES = ["HEALTHCARE", "RETAIL_ECOMMERCE", "TECHNOLOGY", "RESTAURANTS_FOOD", "MANUFACTURING", "EDUCATION"]
NAMES = [
    "Sample Retail Co", "Sample Diagnostics", "Sample Cafe Group", "Sample Software Labs", "Sample Traders",
    "Sample Clinic", "Sample Foods", "Sample Fabrication", "Sample Learning Hub", "Sample Pharmacy",
    "Sample Logistics", "Sample Studio", "Sample Wholesale", "Sample Dental Care", "Sample Bakery",
    "Sample Electronics", "Sample Tutors", "Sample Hardware",
]


class Command(BaseCommand):
    help = (
        "Create (or with --delete, remove) clearly-labelled SAMPLE clients, subscriptions, invoices and "
        "payments in the DEVELOPMENT database, so the superadmin analytics page has something to show. "
        "All sample organizations have slugs starting 'sample-'."
    )

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Remove all sample data instead of creating it.")

    def handle(self, *args, delete, **options):
        sample = Organization.objects.using(ALIAS).filter(slug__startswith=PREFIX)
        if delete:
            count = sample.count()
            with transaction.atomic(using=ALIAS):
                sample.delete()
                Coupon.objects.using(ALIAS).filter(code=COUPON_CODE).delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted {count} sample organizations and everything attached to them."))
            return
        if sample.exists():
            raise CommandError(f"{sample.count()} sample organizations already exist. Run with --delete first.")

        plans = list(Plan.objects.using(ALIAS).filter(is_active=True, monthly_price__gt=0).order_by("monthly_price"))
        if not plans:
            raise CommandError("No paid plans in the dev database to attach subscriptions to.")

        rng = random.Random(7)
        now = timezone.now()
        today = timezone.localdate()
        # (status, billing cycle, months of history) — a believable mix of a small platform
        mix = [
            ("ACTIVE", "MONTHLY", 11), ("ACTIVE", "MONTHLY", 10), ("ACTIVE", "YEARLY", 9), ("ACTIVE", "MONTHLY", 9),
            ("ACTIVE", "MONTHLY", 8), ("ACTIVE", "MONTHLY", 7), ("ACTIVE", "YEARLY", 6), ("ACTIVE", "MONTHLY", 6),
            ("ACTIVE", "MONTHLY", 5), ("PAST_DUE", "MONTHLY", 5), ("ACTIVE", "MONTHLY", 4), ("PAYMENT_DUE", "MONTHLY", 3),
            ("TRIAL", "MONTHLY", 0), ("TRIAL", "MONTHLY", 0), ("TRIAL", "MONTHLY", 0),
            ("CANCELLED", "MONTHLY", 6), ("CANCELLED", "MONTHLY", 4), ("ACTIVE", "MONTHLY", 1),
        ]
        invoices_made = payments_made = 0
        with transaction.atomic(using=ALIAS):
            coupon = Coupon.objects.using(ALIAS).create(
                code=COUPON_CODE, description="Sample coupon (safe to delete)",
                discount_type=Coupon.DiscountType.PERCENT, discount_value=Decimal("10"),
            )
            for i, (status, cycle, history) in enumerate(mix):
                joined = today - datetime.timedelta(days=30 * history + rng.randint(0, 12)) if history else today - datetime.timedelta(days=rng.randint(1, 9))
                org = Organization.objects.using(ALIAS).create(
                    name=NAMES[i], slug=f"{PREFIX}{i + 1}", organization_code=f"SMP{i + 1:05d}",
                    business_type=BUSINESS_TYPES[i % len(BUSINESS_TYPES)],
                    size=["SOLO", "SMALL", "MEDIUM"][i % 3],
                    storage_mode=["OUR_DATABASE", "GOOGLE_SHEETS"][i % 2],
                )
                Organization.objects.using(ALIAS).filter(pk=org.pk).update(
                    created_at=timezone.make_aware(datetime.datetime.combine(joined, datetime.time(10, 0)))
                )
                plan = plans[i % len(plans)]
                yearly = cycle == "YEARLY"
                price = plan.yearly_price if yearly else plan.monthly_price  # gross / list price
                net = plan.effective_yearly_price if yearly else plan.effective_monthly_price  # what it really costs
                plan_discount = price - net
                sub = Subscription.objects.using(ALIAS).create(
                    organization=org, plan=plan, status=status, billing_cycle=cycle, price=price, discount=plan_discount, is_current=True,
                    start_date=joined,
                    trial_start_date=joined if status == "TRIAL" else None,
                    trial_end_date=today + datetime.timedelta(days=rng.randint(2, 12)) if status == "TRIAL" else None,
                    cancellation_date=(today.replace(day=3) if status == "CANCELLED" and i == 15 else
                                       today - datetime.timedelta(days=40) if status == "CANCELLED" else None),
                )
                # Bill on a fixed day of each calendar month (days 1-6, so this
                # month's invoice already exists today); yearly plans bill once.
                bill_day = rng.randint(1, 6)
                billing_points = []
                y, m = joined.year, joined.month
                while (y, m) <= (today.year, today.month):
                    d = datetime.date(y, m, bill_day)
                    if d >= joined and d <= today:
                        billing_points.append(d)
                    m += 1
                    if m == 13:
                        y, m = y + 1, 1
                if cycle == "YEARLY":
                    billing_points = billing_points[:1]
                if status in ("CANCELLED", "PAST_DUE") and len(billing_points) > 1:
                    # stop a month early: cancelled clients bill no more, past-due ones have an invoice already past its due date
                    billing_points = billing_points[:-1]
                for k, due_day in enumerate(billing_points):
                    latest = k == len(billing_points) - 1
                    if status in ("PAST_DUE", "PAYMENT_DUE") and latest:
                        inv_status = "OVERDUE" if status == "PAST_DUE" else "ISSUED"
                    elif status == "CANCELLED" and latest:
                        inv_status = "PAID"
                    else:
                        inv_status = "PAID"
                    inv = Invoice.objects.using(ALIAS).create(
                        organization=org, subscription=sub, invoice_date=due_day,
                        # gross list price less the plan's own discount = net, the real price
                        due_date=due_day + datetime.timedelta(days=7), subtotal=price, discount=plan_discount,
                        status=inv_status, currency="INR",
                    )
                    # Some invoices also use a coupon — the only discount that is genuinely given away.
                    if inv_status == "PAID" and (due_day.month == today.month and i % 4 == 0 or rng.random() < 0.12):
                        amount = coupon.discount_amount_for(inv.subtotal - inv.discount)
                        CouponRedemption.objects.using(ALIAS).create(
                            coupon=coupon, organization=org, invoice=inv, discount_amount=amount
                        )
                        inv.discount += amount
                        inv.save(using=ALIAS)
                    invoices_made += 1
                    if inv_status == "PAID":
                        refund = Decimal(500 if (due_day.month == today.month and i == 2) else rng.choice([0, 0, 0, 0, 300, 500]))
                        pay_day = timezone.make_aware(datetime.datetime.combine(due_day, datetime.time(14, 0)))
                        pay = Payment.objects.using(ALIAS).create(
                            organization=org, subscription=sub, invoice=inv, amount=inv.total,
                            payment_method=rng.choice(["UPI", "UPI", "CARD", "BANK_TRANSFER", "NETBANKING"]),
                            gateway=rng.choice(["RAZORPAY", "RAZORPAY", "MANUAL"]),
                            payment_date=min(pay_day, now),
                            status="PARTIALLY_REFUNDED" if refund else "SUCCESS",
                            refunded_amount=refund,
                        )
                        Invoice.objects.using(ALIAS).filter(pk=inv.pk).update(amount_paid=inv.total, amount_due=0)
                        payments_made += 1
        self.stdout.write(self.style.SUCCESS(
            f"Created {len(mix)} sample clients, {invoices_made} invoices, {payments_made} payments in the dev database."
        ))
        self.stdout.write("Remove them later with:  python manage.py sample_platform_data --delete")
