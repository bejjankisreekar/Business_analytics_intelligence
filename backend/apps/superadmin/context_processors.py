from django.urls import NoReverseMatch, reverse

from .audit import ENVS, current_env, env_from_path

# Pages tied to one record (which won't exist in the other environment)
# switch to their section's list page instead.
SECTION_OF = {
    "org_detail": "org_list", "org_edit": "org_list", "org_create": "org_list", "financial_history": "org_list",
    "service_control": "org_list", "edit_client_user": "org_list", "reset_client_password": "org_list",
    "subscription_create": "org_list", "client_invoice_new": "invoice_list",
    "plan_create": "plan_list", "plan_edit": "plan_list",
    "coupon_create": "coupon_list", "coupon_edit": "coupon_list", "coupon_detail": "coupon_list",
    "invoice_detail": "invoice_list", "invoice_edit": "invoice_list", "invoice_new": "invoice_list",
    "payment_edit": "payment_list", "subscription_edit": "subscription_list",
    "storage_change_detail": "storage_change_requests", "webhook_detail": "webhook_list",
    "support_detail": "support_requests",
}

# Sidebar: (section, [(url_name, label, icon, active url_names, badge key, badge tone)])
NAV = [
    ("Insights", [
        ("platform_analytics", "Analytics", "chart", {"platform_analytics"}, None, None),
    ]),
    ("Customers", [
        ("org_list", "Clients", "building",
         {"org_list", "org_detail", "org_create", "org_edit", "financial_history", "service_control",
          "edit_client_user", "reset_client_password", "subscription_create"}, None, None),
        ("subscription_list", "Subscriptions", "refresh", {"subscription_list", "subscription_edit"}, None, None),
        ("storage_change_requests", "Storage requests", "database",
         {"storage_change_requests", "storage_change_detail"}, "storage_requests", "warn"),
        ("support_requests", "Support", "mail",
         {"support_requests", "support_detail"}, "support_requests", "warn"),
    ]),
    ("Revenue", [
        ("invoice_list", "Invoices", "document",
         {"invoice_list", "invoice_detail", "invoice_edit", "invoice_new", "client_invoice_new"}, "overdue_invoices", "bad"),
        ("payment_list", "Payments", "card", {"payment_list", "payment_edit"}, "failed_payments", "bad"),
        ("plan_list", "Plans", "layers", {"plan_list", "plan_create", "plan_edit"}, None, None),
        ("coupon_list", "Coupons", "ticket", {"coupon_list", "coupon_create", "coupon_edit", "coupon_detail"}, None, None),
    ]),
    ("Operations", [
        ("audit_log", "Audit log", "clipboard", {"audit_log"}, None, None),
        ("webhook_list", "Webhooks", "webhook", {"webhook_list", "webhook_detail"}, "failed_webhooks", "bad"),
        ("system_health", "System health", "heart", {"system_health"}, None, None),
        ("mobile_app", "Mobile app", "layers", {"mobile_app"}, None, None),
        ("admin_list", "Admins", "shield", {"admin_list"}, None, None),
    ]),
]


def _env_switch(request, env):
    match = request.resolver_match
    out = {}
    for target in ENVS:
        href = reverse("superadmin:org_list", args=[target])
        if match and match.namespace == "superadmin":
            name = match.url_name
            if name == "overview":
                href = reverse("superadmin:overview")
            else:
                name = SECTION_OF.get(name, name)
                try:
                    href = reverse(f"superadmin:{name}", args=[target])
                except NoReverseMatch:
                    pass
            if request.GET and name == match.url_name:
                href += "?" + request.GET.urlencode()
        out[target] = href
    return out


def console(request):
    """Sidebar, env switch and badge counts for console pages only — every
    other page of the app pays nothing for this."""
    if not request.path.startswith("/superadmin/"):
        return {}
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or getattr(user, "role", "") != "SUPER_ADMIN":
        return {}
    from .alerts import nav_counts

    env = env_from_path(request.path) or "prod"
    counts = nav_counts(env)
    current = request.resolver_match.url_name if request.resolver_match else ""
    nav = []
    for section, links in NAV:
        items = []
        for url_name, label, icon, active_names, badge, tone in links:
            items.append({
                "href": reverse(f"superadmin:{url_name}", args=[env]),
                "label": label, "icon": icon, "active": current in active_names,
                "count": counts.get(badge, 0) if badge else 0, "tone": tone,
            })
        nav.append((section, items))
    return {
        "sa_env": env,
        "sa_nav": nav,
        "sa_nav_counts": counts,
        "sa_env_switch": _env_switch(request, env),
        "sa_signin_env": current_env(),
    }
