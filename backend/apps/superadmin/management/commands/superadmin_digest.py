from django.core.management.base import BaseCommand, CommandError

from apps.superadmin.digest import send_digest


class Command(BaseCommand):
    help = (
        "Email (and post to Slack, when SUPERADMIN_SLACK_WEBHOOK_URL is set) the superadmin alert digest: "
        "failed payments, overdue invoices, trials ending, pending storage requests, Drive sync errors. "
        "Schedule it daily, e.g. `python manage.py superadmin_digest --env prod --base-url https://app.example.com`."
    )

    def add_arguments(self, parser):
        parser.add_argument("--env", choices=["dev", "prod"], default="prod")
        parser.add_argument("--to", nargs="*", help="Send only to these addresses (default: settings / all superadmins).")
        parser.add_argument("--base-url", default="", help="Site URL, so the email can link to each item.")
        parser.add_argument("--no-slack", action="store_true")

    def handle(self, *args, env, to, base_url, no_slack, **options):
        result = send_digest(env, recipients=to or None, base_url=base_url, slack=not no_slack)
        if not (result["sent_email"] or result["sent_slack"]):
            raise CommandError(result["error"] or "Nothing sent: no recipients and no Slack webhook.")
        self.stdout.write(self.style.SUCCESS(
            f"Digest sent ({result['item_count']} items; email={result['sent_email']}, slack={result['sent_slack']})"
            + (f" — {result['error']}" if result["error"] else "")
        ))
