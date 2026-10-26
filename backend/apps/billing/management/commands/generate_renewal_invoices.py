from django.core.management.base import BaseCommand

from apps.billing import services as billing_services


class Command(BaseCommand):
    """Issue renewal invoices ahead of time for every subscription expiring
    within the next couple of days, so a client sees (and can pay) their
    bill before the billing lock ever kicks in. Meant to run once a day —
    there is no in-process scheduler in this project, so wire this up as an
    OS-level scheduled task (Windows Task Scheduler / cron) hitting both
    environments, e.g.:

        manage.py generate_renewal_invoices --using dev
        manage.py generate_renewal_invoices --using prod

    Safe to run more than once a day: issuing is idempotent per
    subscription (see generate_upcoming_renewal_invoices)."""

    help = "Issue renewal invoices for subscriptions expiring within --lead-days (default 2)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--using", default=None,
            help="Database alias to run against (dev/prod). Omit to run against both.",
        )
        parser.add_argument(
            "--lead-days", type=int, default=billing_services.UPCOMING_INVOICE_LEAD_DAYS,
            help="How many days before expiry to issue the invoice (default 2).",
        )

    def handle(self, *args, **options):
        aliases = [options["using"]] if options["using"] else ["dev", "prod"]
        for alias in aliases:
            # One environment's failure (e.g. pending migrations) must not
            # stop the other from running, and must not bury the daily log
            # under a full traceback when a one-line error will do.
            try:
                created = billing_services.generate_upcoming_renewal_invoices(
                    using=alias, lead_days=options["lead_days"]
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"[{alias}] failed: {exc}"))
                continue
            self.stdout.write(self.style.SUCCESS(f"[{alias}] created {created} renewal invoice(s)."))
