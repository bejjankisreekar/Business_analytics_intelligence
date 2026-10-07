from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.billing.models import Plan

# Everything about a plan that a client or the billing code can see.
FIELDS = [
    "name", "is_active", "show_on_landing_page", "monthly_price", "yearly_price",
    "monthly_discount_percent", "yearly_discount_percent", "trial_days",
    "historical_months_limit", "user_limit", "features",
]


class Command(BaseCommand):
    help = (
        "Copy the plan catalog from one environment's database to another so their plans match "
        "(upserts by slug; existing subscriptions keep pointing at the same plan rows). "
        "Dry run by default — pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="source", default="dev", choices=["dev", "prod"])
        parser.add_argument("--to", dest="target", default="prod", choices=["dev", "prod"])
        parser.add_argument("--apply", action="store_true", help="Actually write the changes.")

    def handle(self, *args, source, target, apply, **options):
        if source == target:
            raise CommandError("--from and --to must be different environments.")
        src = {p.slug: p for p in Plan.objects.using(source)}
        dst = {p.slug: p for p in Plan.objects.using(target)}

        changes = []
        for slug, plan in src.items():
            existing = dst.get(slug)
            if existing is None:
                changes.append((slug, "create", list(FIELDS)))
                continue
            diff = [f for f in FIELDS if getattr(plan, f) != getattr(existing, f)]
            if diff:
                changes.append((slug, "update", diff))
        extra = sorted(set(dst) - set(src))

        if not changes:
            self.stdout.write(self.style.SUCCESS(f"{target} plans already match {source}."))
        for slug, action, fields in changes:
            self.stdout.write(f"{action} '{slug}': {', '.join(fields)}")
        if extra:
            self.stdout.write(
                f"Note: {target} also has plans not in {source} ({', '.join(extra)}); they are left untouched."
            )
        if not changes or not apply:
            if changes:
                self.stdout.write("Dry run — nothing written. Re-run with --apply.")
            return

        with transaction.atomic(using=target):
            qs = Plan.objects.using(target)
            # Plan names are unique: park the affected rows first so swapping names can't collide.
            for slug, action, _ in changes:
                if action == "update":
                    qs.filter(slug=slug).update(name=f"__sync_{slug}")
            for slug, action, _ in changes:
                qs.update_or_create(slug=slug, defaults={f: getattr(src[slug], f) for f in FIELDS})
        self.stdout.write(self.style.SUCCESS(f"Synced {len(changes)} plan(s) from {source} to {target}."))
