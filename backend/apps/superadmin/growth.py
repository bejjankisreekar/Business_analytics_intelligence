"""Subscription-business growth metrics for the analytics page: churn, MRR
movements (new / expansion / contraction / churned), the trial-to-paid
funnel and signup-cohort retention.

There's no month-by-month snapshot table, so these are rebuilt from the
subscription history itself. Changing a client's plan supersedes their
subscription with a new one (see billing.services.create_subscription), so
walking each client's subscriptions in order shows every price change:

- new MRR: a client's first paying subscription starts that month;
- expansion / contraction: a later subscription starts at a higher / lower
  monthly price than the one before it;
- churned MRR: a paying subscription is cancelled that month.

A subscription counts as paying unless it's a trial or complimentary.
"""
import calendar
import datetime
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from apps.billing.models import Payment, Subscription
from apps.organizations.models import Organization

from .analytics import COLLECTED, PAYING, ZERO, month_keys, monthly_equivalent

COHORT_MONTHS = 6


def _paying_mrr(sub) -> Decimal:
    if sub.is_complimentary or sub.status == Subscription.Status.TRIAL:
        return ZERO
    return monthly_equivalent(sub)


def _month_end(y, m) -> datetime.date:
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def growth_metrics(env: str, months: int = 12) -> dict:
    today = timezone.localdate()
    keys = month_keys(months, today)
    key_set = set(keys)
    subs = list(
        Subscription.objects.using(env).order_by("organization_id", "created_at")
        .only("organization_id", "status", "price", "discount", "billing_cycle", "is_complimentary",
              "start_date", "trial_start_date", "trial_end_date", "end_date", "cancellation_date",
              "is_current", "created_at")
    )

    moves = {k: {"new": ZERO, "expansion": ZERO, "contraction": ZERO, "churned": ZERO} for k in keys}
    new_paying = defaultdict(int)
    churned_count = defaultdict(int)
    by_org = defaultdict(list)
    for s in subs:
        by_org[s.organization_id].append(s)

    for org_subs in by_org.values():
        prev = None
        for s in org_subs:
            mrr = _paying_mrr(s)
            started = s.start_date or s.created_at.date()
            k = (started.year, started.month)
            if mrr > 0:
                if prev is None or prev == 0:
                    if k in key_set:
                        moves[k]["new"] += mrr
                        new_paying[k] += 1
                else:
                    delta = mrr - prev
                    if k in key_set and delta > 0:
                        moves[k]["expansion"] += delta
                    elif k in key_set and delta < 0:
                        moves[k]["contraction"] += -delta
                prev = mrr
            if s.cancellation_date and mrr > 0:
                ck = (s.cancellation_date.year, s.cancellation_date.month)
                if ck in key_set:
                    moves[ck]["churned"] += mrr
                    churned_count[ck] += 1
                prev = 0

    current_mrr = sum((_paying_mrr(s) for s in subs if s.is_current and s.status in PAYING), ZERO)
    paying_now = sum(1 for s in subs if s.is_current and s.status in PAYING and _paying_mrr(s) > 0)
    this_key = keys[-1]
    m = moves[this_key]
    net_new_this = m["new"] + m["expansion"] - m["contraction"] - m["churned"]
    mrr_at_start = current_mrr - net_new_this
    paying_at_start = paying_now + churned_count[this_key] - new_paying[this_key]

    # Average monthly logo churn across the window (months with a base only).
    rates = []
    running_paying = paying_now
    for k in reversed(keys):
        base = running_paying + churned_count[k] - new_paying[k]
        if base > 0:
            rates.append(churned_count[k] / base * 100)
        running_paying = base

    # ---- trial funnel (trials that ended inside the window) --------------
    window_start = datetime.date(keys[0][0], keys[0][1], 1)
    orgs_created = Organization.objects.using(env).filter(created_at__date__gte=window_start).count()
    trial_orgs = {s.organization_id for s in subs if s.trial_start_date and s.trial_start_date >= window_start}
    ended = {s.organization_id for s in subs
             if s.trial_end_date and window_start <= s.trial_end_date < today and s.organization_id in trial_orgs}
    paid_orgs = set(
        Payment.objects.using(env).filter(organization_id__in=ended, status__in=COLLECTED)
        .values_list("organization_id", flat=True)
    )
    paid_orgs |= {s.organization_id for s in subs
                  if s.organization_id in ended and s.is_current and s.status in PAYING and _paying_mrr(s) > 0}
    converted = len(paid_orgs & ended)

    return {
        "labels": [f"{calendar.month_abbr[mo]} {y}" for y, mo in keys],
        "moves": {
            "new": [float(moves[k]["new"]) for k in keys],
            "expansion": [float(moves[k]["expansion"]) for k in keys],
            "contraction": [-float(moves[k]["contraction"]) for k in keys],
            "churned": [-float(moves[k]["churned"]) for k in keys],
            "net": [float(moves[k]["new"] + moves[k]["expansion"] - moves[k]["contraction"] - moves[k]["churned"])
                    for k in keys],
        },
        "this_month": {
            "new": m["new"], "expansion": m["expansion"], "contraction": m["contraction"], "churned": m["churned"],
            "net_new": net_new_this,
            "logo_churn": (churned_count[this_key] / paying_at_start * 100) if paying_at_start > 0 else None,
            "revenue_churn": (float(m["churned"] / mrr_at_start * 100)) if mrr_at_start > 0 else None,
            "churned_count": churned_count[this_key],
            "new_paying": new_paying[this_key],
        },
        "avg_logo_churn": (sum(rates) / len(rates)) if rates else None,
        "funnel": {
            "signups": orgs_created,
            "trials": len(trial_orgs),
            "trials_ended": len(ended),
            "converted": converted,
            "rate": (converted / len(ended) * 100) if ended else None,
        },
        "cohorts": cohorts(env, subs, today),
    }


def cohorts(env: str, subs, today, months: int = COHORT_MONTHS) -> dict:
    """Signup-month cohorts: for each of the last `months` signup months,
    the share of those clients with a live subscription (trial, paid or
    complimentary) in each month since they signed up."""
    keys = month_keys(months, today)
    first = datetime.date(keys[0][0], keys[0][1], 1)
    orgs = list(Organization.objects.using(env).filter(created_at__date__gte=first).values_list("id", "created_at"))
    periods = defaultdict(list)
    for s in subs:
        start = min(d for d in (s.trial_start_date, s.start_date, s.created_at.date()) if d)
        end = s.cancellation_date or (None if s.is_current else s.end_date) or today
        periods[s.organization_id].append((start, end))

    rows = []
    for y, mo in keys:
        members = [oid for oid, created in orgs if (timezone.localtime(created).year, timezone.localtime(created).month) == (y, mo)]
        cells = []
        for offset in range(months):
            yy, mm = y + (mo - 1 + offset) // 12, (mo - 1 + offset) % 12 + 1
            if (yy, mm) > (today.year, today.month):
                cells.append(None)
                continue
            m_start, m_end = datetime.date(yy, mm, 1), _month_end(yy, mm)
            live = sum(
                1 for oid in members
                if any(start <= m_end and end >= m_start for start, end in periods.get(oid, []))
            )
            cells.append(round(live / len(members) * 100) if members else None)
        rows.append({"label": f"{calendar.month_abbr[mo]} {y}", "size": len(members), "cells": cells})
    return {"offsets": list(range(months)), "rows": rows}


def empty_growth(months: int = 12) -> dict:
    today = timezone.localdate()
    keys = month_keys(months, today)
    zeros = [0.0] * months
    return {
        "labels": [f"{calendar.month_abbr[mo]} {y}" for y, mo in keys],
        "moves": {"new": zeros, "expansion": zeros, "contraction": zeros, "churned": zeros, "net": zeros},
        "this_month": {"new": ZERO, "expansion": ZERO, "contraction": ZERO, "churned": ZERO, "net_new": ZERO,
                       "logo_churn": None, "revenue_churn": None, "churned_count": 0, "new_paying": 0},
        "avg_logo_churn": None,
        "funnel": {"signups": 0, "trials": 0, "trials_ended": 0, "converted": 0, "rate": None},
        "cohorts": {"offsets": list(range(COHORT_MONTHS)), "rows": []},
    }
