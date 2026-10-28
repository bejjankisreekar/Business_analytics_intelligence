from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.test import Client, TestCase

from apps.accounts.models import User
from apps.organizations.models import Organization

from apps.finance.models import Category, PaymentMode, SalesEntry, Subcategory

from .models import ProductSuggestion, QuoteRequest, QuoteRequestSettings


def make_org(slug, business_type):
    return Organization.objects.create(
        name=slug.title(), slug=slug, organization_code=slug.upper()[:12],
        storage_mode=Organization.StorageMode.OUR_DATABASE, business_type=business_type,
    )


@mock.patch("apps.billing.services.is_account_suspended", return_value=False)
@mock.patch("apps.billing.services.has_active_access", return_value=True)
class QuoteRequestFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.shop = make_org("corner-shop", Organization.BusinessType.RETAIL_ECOMMERCE)
        self.hospital = make_org("city-hospital", Organization.BusinessType.HEALTHCARE)
        self.owner = User.objects.create_user(
            email="o@shop.example", password="x-pass-123", organization=self.shop, role=User.Role.OWNER)
        self.hosp_owner = User.objects.create_user(
            email="o@hosp.example", password="x-pass-123", organization=self.hospital, role=User.Role.OWNER)
        QuoteRequestSettings.objects.create(organization=self.shop, is_enabled=True)
        self.anon = Client()

    def _submit(self, slug="corner-shop", **over):
        data = {
            "name": "Ravi", "phone": "9876543210",
            "product": ["Colgate paste", "Sugar", ""], "qty": ["2", "5", ""], "unit": ["pc", "kg", "kg"],
        }
        data.update(over)
        return self.anon.post(f"/request/{slug}/", data)

    def test_open_page_needs_no_login(self, *_):
        self.assertEqual(self.anon.get("/request/corner-shop/").status_code, 200)

    def test_link_off_or_wrong_business_type_is_404(self, *_):
        QuoteRequestSettings.objects.filter(organization=self.shop).update(is_enabled=False)
        self.assertEqual(self.anon.get("/request/corner-shop/").status_code, 404)
        QuoteRequestSettings.objects.create(organization=self.hospital, is_enabled=True)
        self.assertEqual(self.anon.get("/request/city-hospital/").status_code, 404)

    def test_submit_creates_request_and_redirects_to_private_link(self, *_):
        resp = self._submit()
        req = QuoteRequest.objects.get()
        self.assertRedirects(resp, f"/r/{req.tracking_token}/?new=1")
        self.assertEqual(req.number, 1)
        self.assertEqual([i.product_name for i in req.items.all()], ["Colgate paste", "Sugar"])

    def test_numbers_are_per_organization_and_blank_rows_are_skipped(self, *_):
        self._submit()
        self._submit()
        self.assertEqual(list(QuoteRequest.objects.order_by("number").values_list("number", flat=True)), [1, 2])

    def test_validation_errors(self, *_):
        self.assertEqual(self._submit(phone="12").status_code, 400)
        self.assertEqual(self._submit(product=["", ""], qty=["", ""], unit=["kg", "kg"]).status_code, 400)
        self.assertEqual(self._submit(product=["Rice"], qty=[""], unit=["kg"]).status_code, 400)
        self.assertEqual(self._submit(product=["Rice"], qty=["abc"], unit=["kg"]).status_code, 400)
        self.assertEqual(self._submit(product=["Rice"], qty=["1"], unit=["parsec"]).status_code, 400)
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_rate_limit(self, *_):
        for _i in range(10):
            self._submit()
        self.assertEqual(self._submit().status_code, 429)

    def test_owner_quotes_and_customer_sees_prices_and_total(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        a, b = req.items.all()
        owner = Client()
        owner.force_login(self.owner)

        self.assertContains(owner.get("/customer-requests/"), "Ravi")
        self.assertEqual(owner.get(f"/customer-requests/{req.number}/").status_code, 200)

        # Sending without a price on an available row is rejected.
        owner.post(f"/customer-requests/{req.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "90", f"available_{b.pk}": "on"})
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.PENDING)

        owner.post(f"/customer-requests/{req.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "90",
            f"remark_{b.pk}": "Out of stock"})  # b not ticked => unavailable
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.QUOTED)
        self.assertEqual(req.total, Decimal("180.00"))  # 90 per pc x 2 pc

        page = self.anon.get(f"/r/{req.tracking_token}/")
        self.assertContains(page, "180.00")
        self.assertContains(page, "Out of stock")

    def test_customer_sees_no_prices_until_quoted(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        req.items.update(price=Decimal("4417.77"))
        self.assertNotContains(self.anon.get(f"/r/{req.tracking_token}/"), "4417")

    def test_owner_cannot_open_another_orgs_request(self, *_):
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        req = QuoteRequest.objects.create(organization=other, number=1, customer_name="X", customer_phone="9999999999")
        owner = Client()
        owner.force_login(self.owner)
        self.assertEqual(owner.get(f"/customer-requests/{req.number}/").status_code, 404)

    def test_hospital_has_no_access_and_no_menu_link(self, *_):
        c = Client()
        c.force_login(self.hosp_owner)
        self.assertEqual(c.get("/customer-requests/").status_code, 403)
        self.assertNotContains(c.get("/app/"), "Customer Requests")

    def test_shop_menu_shows_link(self, *_):
        c = Client()
        c.force_login(self.owner)
        self.assertContains(c.get("/app/"), "Customer Requests")

    def test_qr_poster_and_download_for_shop(self, *_):
        c = Client()
        c.force_login(self.owner)
        page = c.get("/customer-requests/qr/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<svg")
        self.assertContains(page, "/request/corner-shop/")
        svg = c.get("/customer-requests/qr.svg")
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertIn(b"<path", svg.content)

    def test_qr_not_available_to_hospital_or_anonymous(self, *_):
        c = Client()
        c.force_login(self.hosp_owner)
        self.assertEqual(c.get("/customer-requests/qr/").status_code, 403)
        self.assertEqual(c.get("/customer-requests/qr.svg").status_code, 403)
        self.assertEqual(self.anon.get("/customer-requests/qr/").status_code, 302)

    def test_many_scans_get_distinct_numbers_and_links(self, *_):
        people = [Client() for _i in range(5)]
        for i, person in enumerate(people):
            person.post("/request/corner-shop/", {
                "name": f"Customer {i}", "phone": f"98765432{i:02d}",
                "product": ["Rice"], "qty": ["1"], "unit": ["kg"]})
        reqs = QuoteRequest.objects.order_by("number")
        self.assertEqual([r.number for r in reqs], [1, 2, 3, 4, 5])
        self.assertEqual(len({r.tracking_token for r in reqs}), 5)
        # Each customer's private link shows only their own list.
        page = self.anon.get(f"/r/{reqs[0].tracking_token}/")
        self.assertContains(page, "Customer 0")
        self.assertNotContains(page, "Customer 1")

    def test_inventory_names_become_suggestions_on_open_page(self, *_):
        toothpaste = Category.objects.create(organization=self.shop, kind=Category.Kind.PRODUCT, name="Toothpaste")
        Subcategory.objects.create(organization=self.shop, category=toothpaste, name="Colgate")
        hidden = Category.objects.create(organization=self.shop, kind=Category.Kind.PRODUCT, name="Retired", is_active=False)
        Subcategory.objects.create(organization=self.shop, category=hidden, name="Old brand")
        Category.objects.create(organization=self.shop, kind=Category.Kind.EXPENSE, name="Rent")

        # Nothing until the owner opens Customer Requests (the only time we can read their data).
        self.assertNotContains(self.anon.get("/request/corner-shop/"), 'value="Colgate"')
        owner = Client()
        owner.force_login(self.owner)
        owner.get("/customer-requests/")

        page = self.anon.get("/request/corner-shop/")
        self.assertContains(page, '<option value="Colgate">')
        self.assertContains(page, '<option value="Toothpaste">')
        self.assertNotContains(page, "Old brand")
        self.assertNotContains(page, "Rent")

        # Removing an item drops it from the list on the next refresh.
        Subcategory.objects.filter(name="Colgate").delete()
        owner.get("/customer-requests/")
        self.assertNotContains(self.anon.get("/request/corner-shop/"), 'value="Colgate"')

    def test_suggestions_are_per_organization(self, *_):
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        ProductSuggestion.objects.create(organization=other, name="Secret dish")
        self.assertNotContains(self.anon.get("/request/corner-shop/"), "Secret dish")

    def test_unit_is_saved_and_rate_times_quantity_is_the_row_amount(self, *_):
        self._submit(product=["Milk"], qty=["3"], unit=["litre"])
        req = QuoteRequest.objects.get()
        item = req.items.get()
        self.assertEqual((item.qty, item.unit), (Decimal("3"), "litre"))
        item.price = Decimal("55")
        item.save()
        self.assertEqual(item.line_total, Decimal("165.00"))

    def test_inbox_lists_oldest_first(self, *_):
        for name in ("First", "Second", "Third"):
            self._submit(name=name)
        owner = Client()
        owner.force_login(self.owner)
        body = owner.get("/customer-requests/").content.decode()
        self.assertLess(body.index("First"), body.index("Second"))
        self.assertLess(body.index("Second"), body.index("Third"))

    def _quoted_request(self):
        self._submit()
        req = QuoteRequest.objects.get()
        a, b = req.items.all()
        owner = Client()
        owner.force_login(self.owner)
        owner.post(f"/customer-requests/{req.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "90",
            f"available_{b.pk}": "on", f"price_{b.pk}": "45"})
        req.refresh_from_db()
        return owner, req, a, b

    def test_owner_marks_payment_paid_and_back_to_pending(self, *_):
        owner, req, *_x = self._quoted_request()
        self.assertEqual(req.payment_status, QuoteRequest.PaymentStatus.PENDING)
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), "Please pay")

        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"})
        req.refresh_from_db()
        self.assertEqual(req.payment_status, QuoteRequest.PaymentStatus.PAID)
        self.assertIsNotNone(req.paid_at)
        self.assertContains(owner.get(f"/customer-requests/{req.number}/"), "Ready to deliver")
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), "ready for delivery")
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), 'pill pill--done">Paid')
        paid_page = self.anon.get(f"/r/{req.tracking_token}/?new=1")
        self.assertContains(paid_page, "Payment received")
        self.assertNotContains(paid_page, "Your list has been sent")

        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "pending"})
        req.refresh_from_db()
        self.assertEqual(req.payment_status, QuoteRequest.PaymentStatus.PENDING)
        self.assertIsNone(req.paid_at)

    def test_cannot_record_payment_before_quote_is_sent(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        owner = Client()
        owner.force_login(self.owner)
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"})
        req.refresh_from_db()
        self.assertEqual(req.payment_status, QuoteRequest.PaymentStatus.PENDING)

    def test_changing_the_amount_after_payment_resets_to_pending(self, *_):
        owner, req, a, b = self._quoted_request()
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"})
        owner.post(f"/customer-requests/{req.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "100",
            f"available_{b.pk}": "on", f"price_{b.pk}": "45"})
        req.refresh_from_db()
        self.assertEqual(req.payment_status, QuoteRequest.PaymentStatus.PENDING)

    def test_other_orgs_owner_cannot_mark_payment(self, *_):
        owner, req, *_x = self._quoted_request()
        intruder = Client()
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        user = User.objects.create_user(email="x@o.example", password="x-pass-123", organization=other, role=User.Role.OWNER)
        intruder.force_login(user)
        self.assertEqual(intruder.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"}).status_code, 404)

    def test_inbox_paid_and_unpaid_cards_and_filters(self, *_):
        owner, req, *_x = self._quoted_request()          # quoted, unpaid
        self._submit(name="Second")                        # still waiting for a quote
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"})
        self._submit(name="Third")
        third = QuoteRequest.objects.get(customer_name="Third")
        QuoteRequest.objects.filter(pk=third.pk).update(status=QuoteRequest.Status.QUOTED)

        page = owner.get("/customer-requests/")
        self.assertEqual((page.context["paid_count"], page.context["unpaid_count"], page.context["total_count"]), (1, 1, 3))
        paid = owner.get("/customer-requests/?payment=PAID")
        self.assertEqual([r.number for r in paid.context["quote_requests"]], [req.number])
        unpaid = owner.get("/customer-requests/?payment=UNPAID")
        self.assertEqual([r.number for r in unpaid.context["quote_requests"]], [third.number])

    def test_inbox_shows_amount_per_row_and_total_card(self, *_):
        owner, req, *_x = self._quoted_request()           # 2 pc x 90 + 5 kg x 45 = 405.00
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid"})
        self._submit(name="Waiting")                       # not quoted: no amount
        page = owner.get("/customer-requests/")
        by_name = {r.customer_name: r for r in page.context["quote_requests"]}
        self.assertEqual(by_name["Ravi"].amount, Decimal("405.00"))
        self.assertEqual(by_name["Waiting"].amount, Decimal("0"))
        self.assertEqual(page.context["amounts"], {"total": Decimal("405.00"), "paid": Decimal("405.00"), "unpaid": Decimal("0")})
        self.assertContains(page, "405.00")

    def test_grams_shown_in_full_on_both_sides(self, *_):
        self._submit(product=["Saffron"], qty=["250"], unit=["g"])
        req = QuoteRequest.objects.get()
        item = req.items.get()
        item.price = Decimal("4")
        item.save()
        req.status = QuoteRequest.Status.QUOTED
        req.save()
        owner = Client()
        owner.force_login(self.owner)
        self.assertContains(owner.get(f"/customer-requests/{req.number}/"), "250 grams")
        page = self.anon.get(f"/r/{req.tracking_token}/")
        self.assertContains(page, "250 grams")
        self.assertContains(page, "per gram")
        self.assertContains(self.anon.get("/request/corner-shop/"), '["g", "grams"]')

    def test_paid_request_is_booked_as_a_sale_in_the_daily_book(self, *_):
        owner, req, *_x = self._quoted_request()          # total 405.00
        self.assertEqual(SalesEntry.objects.count(), 0)    # quoting alone books nothing

        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "BANK"})
        sale = SalesEntry.objects.get()
        self.assertEqual(sale.amount, Decimal("405.00"))
        self.assertEqual(sale.payment_mode, PaymentMode.BANK)
        self.assertEqual(sale.channel.name, "Customer requests")   # one lump-sum channel
        self.assertEqual(sale.channel.kind, Category.Kind.SALES)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(sale.date, timezone.localdate())
        self.assertIn(f"#{req.number}", sale.note)
        req.refresh_from_db()
        self.assertEqual(req.sales_entry_id, sale.pk)
        self.assertEqual(req.payment_mode, "BANK")
        self.assertContains(owner.get(f"/customer-requests/{req.number}/"), "Added to your Daily Book")

    def test_marking_paid_twice_books_only_one_sale(self, *_):
        owner, req, *_x = self._quoted_request()
        for _i in range(2):
            owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "CASH"})
        self.assertEqual(SalesEntry.objects.count(), 1)
        self.assertEqual(Category.objects.count(), 1)

    def test_reverting_payment_removes_the_sale(self, *_):
        owner, req, *_x = self._quoted_request()
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "CASH"})
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "pending"})
        self.assertEqual(SalesEntry.objects.count(), 0)
        req.refresh_from_db()
        self.assertIsNone(req.sales_entry_id)
        # Paying again books a fresh sale, once.
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "CASH"})
        self.assertEqual(SalesEntry.objects.count(), 1)

    def test_changing_the_amount_after_payment_removes_the_sale(self, *_):
        owner, req, a, b = self._quoted_request()
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "CASH"})
        owner.post(f"/customer-requests/{req.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "100",
            f"available_{b.pk}": "on", f"price_{b.pk}": "45"})
        self.assertEqual(SalesEntry.objects.count(), 0)

    def test_unknown_payment_mode_falls_back_to_cash(self, *_):
        owner, req, *_x = self._quoted_request()
        owner.post(f"/customer-requests/{req.number}/payment/", {"payment": "paid", "mode": "bitcoin"})
        self.assertEqual(SalesEntry.objects.get().payment_mode, PaymentMode.CASH)

    def test_quote_pdf_for_owner_and_customer(self, *_):
        owner, req, *_x = self._quoted_request()
        mine = owner.get(f"/customer-requests/{req.number}/pdf/")
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine["Content-Type"], "application/pdf")
        self.assertTrue(mine.content.startswith(b"%PDF"))
        theirs = self.anon.get(f"/r/{req.tracking_token}/quote.pdf")
        self.assertEqual(theirs.status_code, 200)
        self.assertTrue(theirs.content.startswith(b"%PDF"))
        # The owner page offers the WhatsApp PDF share, the customer page the download.
        self.assertContains(owner.get(f"/customer-requests/{req.number}/"), "Share PDF on WhatsApp")
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), "Download quotation (PDF)")

    def test_quote_pdf_needs_a_sent_quote_and_the_right_owner(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        owner = Client()
        owner.force_login(self.owner)
        self.assertEqual(owner.get(f"/customer-requests/{req.number}/pdf/").status_code, 404)
        self.assertEqual(self.anon.get(f"/r/{req.tracking_token}/quote.pdf").status_code, 404)
        self.assertEqual(self.anon.get("/r/not-a-real-token/quote.pdf").status_code, 404)

        other_owner, other_req, *_x = self._quoted_request_in_other_org()
        self.assertEqual(owner.get(f"/customer-requests/{other_req.number}/pdf/").status_code, 404)

    def _quoted_request_in_other_org(self):
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        QuoteRequestSettings.objects.create(organization=other, is_enabled=True)
        user = User.objects.create_user(email="x@o.example", password="x-pass-123", organization=other, role=User.Role.OWNER)
        req = QuoteRequest.objects.create(
            organization=other, number=1, customer_name="Z", customer_phone="9999999999", status=QuoteRequest.Status.QUOTED)
        c = Client()
        c.force_login(user)
        return c, req

    # ---- customer edits their list before the quote ----

    def _edit(self, req, **over):
        data = {"name": "Ravi", "phone": "9876543210",
                "product": ["Rice", "Dal"], "qty": ["3", "250"], "unit": ["kg", "g"]}
        data.update(over)
        return self.anon.post(f"/r/{req.tracking_token}/edit/", data)

    def test_customer_can_edit_list_before_quote(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        page = self.anon.get(f"/r/{req.tracking_token}/edit/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Colgate paste")           # current items come back filled in
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), "Edit my list")

        resp = self._edit(req, name="Ravi K")
        self.assertRedirects(resp, f"/r/{req.tracking_token}/?updated=1")
        req.refresh_from_db()
        self.assertEqual((req.number, req.customer_name), (1, "Ravi K"))
        self.assertIsNotNone(req.edited_at)
        self.assertEqual([(i.product_name, i.qty, i.unit) for i in req.items.all()],
                         [("Rice", Decimal("3"), "kg"), ("Dal", Decimal("250"), "g")])
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/?updated=1"), "Your list was updated")

    def test_whole_numbers_are_not_shown_in_scientific_notation_when_editing(self, *_):
        self._submit(product=["Saffron"], qty=["250"], unit=["g"])
        req = QuoteRequest.objects.get()
        self.assertNotContains(self.anon.get(f"/r/{req.tracking_token}/edit/"), "E+")
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/edit/"), '"qty": "250"')

    def test_invalid_edit_keeps_the_old_list(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        self.assertEqual(self._edit(req, product=["Rice"], qty=[""], unit=["kg"]).status_code, 400)
        self.assertEqual(self._edit(req, phone="12").status_code, 400)
        self.assertEqual([i.product_name for i in req.items.all()], ["Colgate paste", "Sugar"])
        req.refresh_from_db()
        self.assertIsNone(req.edited_at)

    def test_customer_cannot_edit_after_the_quote_is_sent(self, *_):
        owner, req, *_x = self._quoted_request()
        self.assertRedirects(self.anon.get(f"/r/{req.tracking_token}/edit/"), f"/r/{req.tracking_token}/")
        self._edit(req)
        self.assertEqual([i.product_name for i in req.items.all()], ["Colgate paste", "Sugar"])
        self.assertNotContains(self.anon.get(f"/r/{req.tracking_token}/"), "Edit my list")

    def test_edit_needs_the_right_token(self, *_):
        self.assertEqual(self.anon.get("/r/not-a-real-token/edit/").status_code, 404)
        self.assertEqual(self.anon.post("/r/not-a-real-token/edit/", {}).status_code, 404)

    def test_owner_cannot_quote_a_list_the_customer_just_changed(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        owner = Client()
        owner.force_login(self.owner)
        stale = owner.get(f"/customer-requests/{req.number}/").context["version"]
        old_ids = [i.pk for i in req.items.all()]

        self._edit(req)                                         # the customer changes the list meanwhile
        data = {"send": "1", "version": stale}
        for pk in old_ids:
            data.update({f"available_{pk}": "on", f"price_{pk}": "10"})
        owner.post(f"/customer-requests/{req.number}/", data)
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.PENDING)   # refused, nothing was sent

        fresh = owner.get(f"/customer-requests/{req.number}/")
        self.assertContains(fresh, "The customer updated this list")
        data = {"send": "1", "version": fresh.context["version"]}
        for item in req.items.all():
            data.update({f"available_{item.pk}": "on", f"price_{item.pk}": "10"})
        owner.post(f"/customer-requests/{req.number}/", data)
        req.refresh_from_db()
        self.assertEqual(req.status, QuoteRequest.Status.QUOTED)
        self.assertEqual(req.total, Decimal("30.00") + Decimal("2500.00"))

    # ---- production base URL ----

    @override_settings(IS_PRODUCTION=True, SITE_URL="https://findayintelligence.com/")
    def test_production_links_and_qr_use_the_public_site_address(self, *_):
        from .qr import qr_svg

        owner, req, *_x = self._quoted_request()
        expected = "https://findayintelligence.com/request/corner-shop/"

        inbox = owner.get("/customer-requests/")
        self.assertEqual(inbox.context["public_url"], expected)          # trailing slash in SITE_URL is handled
        self.assertContains(inbox, expected)

        poster = owner.get("/customer-requests/qr/")
        self.assertEqual(poster.context["public_url"], expected)
        self.assertContains(poster, expected)

        download = owner.get("/customer-requests/qr.svg")
        self.assertEqual(download.content.decode(), qr_svg(expected, size_attrs=True))   # the QR itself encodes it

        detail = owner.get(f"/customer-requests/{req.number}/")
        self.assertTrue(detail.context["tracking_url"].startswith("https://findayintelligence.com/r/"))
        self.assertTrue(detail.context["public_pdf_url"].startswith("https://findayintelligence.com/r/"))
        self.assertNotIn("testserver", detail.context["tracking_url"] + detail.context["public_pdf_url"])

    @override_settings(IS_PRODUCTION=False, SITE_URL="https://findayintelligence.com")
    def test_outside_production_links_follow_the_address_you_opened(self, *_):
        owner = Client()
        owner.force_login(self.owner)
        self.assertTrue(owner.get("/customer-requests/").context["public_url"].startswith("http://testserver/request/"))

    # ---- optional delivery address ----

    def test_address_is_optional_and_saved_as_one_tidy_line(self, *_):
        self.assertContains(self.anon.get("/request/corner-shop/"), 'id="s-address"')
        self._submit(address="  12, Gandhi Road\n   Near the temple  ")
        self._submit(name="NoAddress")
        saved = {r.customer_name: r.customer_address for r in QuoteRequest.objects.all()}
        self.assertEqual(saved["Ravi"], "12, Gandhi Road Near the temple")
        self.assertEqual(saved["NoAddress"], "")           # leaving it out is fine

    def test_very_long_address_is_cut_to_fit(self, *_):
        self._submit(address="x" * 400)
        self.assertEqual(len(QuoteRequest.objects.get().customer_address), 255)

    def test_customer_can_change_the_address_when_editing(self, *_):
        self._submit(address="Old street")
        req = QuoteRequest.objects.get()
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/edit/"), "Old street")
        self._edit(req, address="New street 5")
        req.refresh_from_db()
        self.assertEqual(req.customer_address, "New street 5")
        self._edit(req, address="")
        req.refresh_from_db()
        self.assertEqual(req.customer_address, "")

    def test_address_shows_to_the_owner_the_customer_and_on_the_pdf(self, *_):
        from django.template.loader import render_to_string

        self._submit(address="12 Gandhi Road")
        req = QuoteRequest.objects.get()
        owner = Client()
        owner.force_login(self.owner)
        self.assertContains(owner.get(f"/customer-requests/{req.number}/"), "12 Gandhi Road")
        self.assertContains(self.anon.get(f"/r/{req.tracking_token}/"), "12 Gandhi Road")
        html = render_to_string("quote_requests/quote_pdf.html", {
            "req": req, "org": req.organization, "items": list(req.items.all()), "item_count": 2, "available_count": 2})
        self.assertIn("Deliver to: 12 Gandhi Road", html)
        req.customer_address = ""
        html = render_to_string("quote_requests/quote_pdf.html", {
            "req": req, "org": req.organization, "items": list(req.items.all()), "item_count": 2, "available_count": 2})
        self.assertNotIn("Deliver to", html)

    # ---- development: a phone can't reach 127.0.0.1 ----

    def _owner_on(self, host):
        owner = Client(HTTP_HOST=host)
        owner.force_login(self.owner)
        return owner

    @override_settings(IS_PRODUCTION=False)
    @mock.patch("apps.quote_requests.views._detect_lan_ip", return_value="10.155.196.76")
    def test_dev_links_use_the_wifi_address_when_opened_on_loopback(self, *_):
        for host in ("127.0.0.1:8000", "localhost:8000"):
            owner = self._owner_on(host)
            self.assertEqual(
                owner.get("/customer-requests/").context["public_url"],
                "http://10.155.196.76:8000/request/corner-shop/", host)
        owner, req, *_x = self._quoted_request()
        detail = self._owner_on("127.0.0.1:8000").get(f"/customer-requests/{req.number}/")
        self.assertTrue(detail.context["tracking_url"].startswith("http://10.155.196.76:8000/r/"))
        self.assertTrue(detail.context["public_pdf_url"].startswith("http://10.155.196.76:8000/r/"))

    @override_settings(IS_PRODUCTION=False)
    @mock.patch("apps.quote_requests.views._detect_lan_ip", return_value="10.155.196.76")
    def test_dev_links_keep_the_address_you_used_when_it_is_not_loopback(self, *_):
        owner = Client()                                   # host "testserver"
        owner.force_login(self.owner)
        self.assertTrue(owner.get("/customer-requests/").context["public_url"].startswith("http://testserver/"))

    @override_settings(IS_PRODUCTION=False)
    @mock.patch("apps.quote_requests.views._detect_lan_ip", return_value=None)
    def test_dev_links_fall_back_to_loopback_if_no_wifi_address_is_found(self, *_):
        url = self._owner_on("127.0.0.1:8000").get("/customer-requests/").context["public_url"]
        self.assertTrue(url.startswith("http://127.0.0.1:8000/"))

    @override_settings(IS_PRODUCTION=False)
    @mock.patch("apps.quote_requests.views._detect_lan_ip", return_value="10.155.196.76")
    @mock.patch("apps.quote_requests.views.config", return_value="192.168.1.50")
    def test_dev_public_host_setting_wins(self, *_):
        url = self._owner_on("127.0.0.1:8000").get("/customer-requests/").context["public_url"]
        self.assertEqual(url, "http://192.168.1.50:8000/request/corner-shop/")

    @override_settings(IS_PRODUCTION=True, SITE_URL="https://findayintelligence.com")
    @mock.patch("apps.quote_requests.views._detect_lan_ip", return_value="10.155.196.76")
    def test_production_ignores_the_wifi_address(self, *_):
        url = self._owner_on("127.0.0.1:8000").get("/customer-requests/").context["public_url"]
        self.assertEqual(url, "https://findayintelligence.com/request/corner-shop/")

    # ---- share the list; five rows to start ----

    def test_share_link_opens_a_view_only_page_without_contact_details(self, *_):
        self._submit(address="12 Gandhi Road", phone="9123456780")
        req = QuoteRequest.objects.get()
        page = self.anon.get(f"/r/{req.tracking_token}/")
        self.assertContains(page, 'id="share-list"')
        self.assertContains(page, 'id="copy-list"')
        req.refresh_from_db()
        self.assertTrue(req.share_token)
        share_url = page.context["share_url"]
        self.assertTrue(share_url.endswith(f"/s/{req.share_token}/"))
        self.assertNotIn(req.tracking_token, share_url)
        shared = self.anon.get(f"/s/{req.share_token}/")
        self.assertEqual(shared.status_code, 200)
        self.assertContains(shared, "Colgate paste")
        self.assertContains(shared, "Sugar")
        for private in ("Ravi", "9123456780", "12 Gandhi Road", req.tracking_token, "Edit my list", 'id="share-list"'):
            self.assertNotContains(shared, private)
        self.assertEqual(self.anon.get("/s/not-a-real-token/").status_code, 404)

    def test_share_link_shows_prices_and_total_once_quoted_but_no_pdf(self, *_):
        owner, req, a, b = self._quoted_request()           # 2 pc x 90, 5 kg x 45
        self.anon.get(f"/r/{req.tracking_token}/")
        req.refresh_from_db()
        shared = self.anon.get(f"/s/{req.share_token}/")
        self.assertContains(shared, "180.00")
        self.assertContains(shared, "405.00")
        self.assertNotContains(shared, "Download quotation")
        self.assertNotContains(shared, req.tracking_token)

    def test_entry_page_starts_with_five_rows_and_offers_sharing(self, *_):
        page = self.anon.get("/request/corner-shop/")
        self.assertContains(page, "var MIN_ROWS = 5;")
        self.assertContains(page, 'id="share-draft"')
        self.assertContains(page, 'id="copy-draft"')
        self.assertContains(self.anon.get(
            f"/r/{self._make_waiting_request().tracking_token}/edit/"), "var MIN_ROWS = 5;")

    def _make_waiting_request(self):
        self._submit()
        return QuoteRequest.objects.get()

    # ---- a refresh in the middle of the list keeps the customer where they are ----

    def test_entry_page_remembers_the_list_in_this_tab(self, *_):
        page = self.anon.get("/request/corner-shop/")
        self.assertContains(page, "var DRAFT_KEY = 'qr-draft:corner-shop';")
        self.assertContains(page, "localStorage")                     # survives a closed tab or a phone reload
        self.assertContains(page, "var DRAFT_HOURS = 24;")
        self.assertContains(page, 'id="draft-bar"')
        self.assertContains(page, "Start over")
        self.assertContains(page, "var SERVER_ERROR = false;")

    def test_a_server_error_page_does_not_restore_an_old_draft(self, *_):
        resp = self._submit(phone="12")                              # rejected: invalid phone
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "var SERVER_ERROR = true;", status_code=400)

    def test_edit_page_keeps_its_own_draft_per_order(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        page = self.anon.get(f"/r/{req.tracking_token}/edit/")
        self.assertContains(page, f"var DRAFT_KEY = 'qr-draft-edit:{req.tracking_token}';")
        self.assertContains(page, "Discard changes")

    def test_order_page_forgets_the_draft_once_the_order_exists(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        page = self.anon.get(f"/r/{req.tracking_token}/?new=1")
        self.assertContains(page, "localStorage.removeItem('qr-draft:corner-shop');")
        self.assertContains(page, f"localStorage.removeItem('qr-draft-edit:{req.tracking_token}');")

    # ---- search on the requests list ----

    def _seed_people(self):
        self._submit(name="Ravi Kumar", phone="98765 43210", address="12 Gandhi Road",
                     product=["Colgate paste"], qty=["2"], unit=["pc"])
        self._submit(name="Meena Devi", phone="90000-11122", address="Lake View Apartments",
                     product=["Basmati rice"], qty=["5"], unit=["kg"])
        self._submit(name="Kavya", phone="+91 88888 77777", address="",
                     product=["Turmeric"], qty=["100"], unit=["g"])
        owner = Client()
        owner.force_login(self.owner)
        return owner

    def _found(self, owner, query, extra=""):
        page = owner.get("/customer-requests/", {"q": query} if not extra else {"q": query, "status": extra})
        return [r.customer_name for r in page.context["quote_requests"]]

    def test_search_by_name_is_case_insensitive_and_partial(self, *_):
        owner = self._seed_people()
        self.assertEqual(self._found(owner, "ravi"), ["Ravi Kumar"])
        self.assertEqual(self._found(owner, "DEVI"), ["Meena Devi"])
        self.assertEqual(self._found(owner, "zzz"), [])

    def test_search_by_phone_ignores_spaces_dashes_and_plus(self, *_):
        owner = self._seed_people()
        self.assertEqual(self._found(owner, "9876543210"), ["Ravi Kumar"])
        self.assertEqual(self._found(owner, "98765 43"), ["Ravi Kumar"])
        self.assertEqual(self._found(owner, "9000011122"), ["Meena Devi"])
        self.assertEqual(self._found(owner, "88888"), ["Kavya"])
        self.assertEqual(self._found(owner, "918888877777"), ["Kavya"])

    def test_search_by_order_number_item_and_address(self, *_):
        owner = self._seed_people()
        self.assertEqual(self._found(owner, "2"), ["Meena Devi"])      # a short number is an order number
        self.assertEqual(self._found(owner, "#3"), ["Kavya"])
        self.assertEqual(self._found(owner, "12"), [])                  # no order #12, and "12 Gandhi Road" is not matched
        self.assertEqual(self._found(owner, "12 gandhi"), ["Ravi Kumar"])
        self.assertEqual(self._found(owner, "basmati"), ["Meena Devi"])
        self.assertEqual(self._found(owner, "turmeric"), ["Kavya"])
        self.assertEqual(self._found(owner, "gandhi"), ["Ravi Kumar"])
        self.assertEqual(self._found(owner, "lake view"), ["Meena Devi"])

    def test_search_never_lists_a_request_twice_and_keeps_order(self, *_):
        self._submit(name="Ravi", product=["Rice", "Rice flour", "Ricebran oil"], qty=["1", "1", "1"], unit=["kg", "kg", "kg"])
        self._submit(name="Zed", product=["Rice"], qty=["1"], unit=["kg"])
        owner = Client()
        owner.force_login(self.owner)
        self.assertEqual(self._found(owner, "rice"), ["Ravi", "Zed"])   # once each, oldest first

    def test_search_combines_with_the_status_filter_and_is_shown_on_the_page(self, *_):
        owner = self._seed_people()
        self.assertEqual(self._found(owner, "a", extra="QUOTED"), [])   # nothing is quoted yet
        page = owner.get("/customer-requests/", {"q": "ravi", "status": "PENDING"})
        self.assertContains(page, 'value="ravi"')
        self.assertContains(page, "1 result for")
        self.assertContains(page, 'name="status" value="PENDING"')      # the filter survives a new search
        self.assertContains(page, "q=ravi")                              # and the cards keep the search
        self.assertContains(owner.get("/customer-requests/", {"q": "nobody"}), "No requests match")

    def test_search_only_looks_inside_your_own_requests(self, *_):
        self._seed_people()
        other = make_org("other-shop", Organization.BusinessType.RESTAURANTS_FOOD)
        QuoteRequest.objects.create(organization=other, number=1, customer_name="Ravi Other", customer_phone="9999999999")
        owner = Client()
        owner.force_login(self.owner)
        self.assertEqual(self._found(owner, "ravi"), ["Ravi Kumar"])

    # ---- the list opens on today; yesterday, a picked date, or all days on request ----

    def _three_days(self):
        """One request today ('Tia'), one yesterday ('Yan'), one three days ago ('Dev')."""
        for name in ("Dev", "Yan", "Tia"):
            self._submit(name=name)
        by_name = {r.customer_name: r for r in QuoteRequest.objects.all()}
        now = timezone.now()
        QuoteRequest.objects.filter(pk=by_name["Yan"].pk).update(created_at=now - timedelta(days=1))
        QuoteRequest.objects.filter(pk=by_name["Dev"].pk).update(created_at=now - timedelta(days=3))
        owner = Client()
        owner.force_login(self.owner)
        return owner, by_name

    def _names(self, owner, **params):
        page = owner.get("/customer-requests/", params)
        return [r.customer_name for r in page.context["quote_requests"]], page

    def test_list_opens_on_today(self, *_):
        owner, _x = self._three_days()
        names, page = self._names(owner)
        self.assertEqual(names, ["Tia"])
        self.assertEqual(page.context["day"], "today")
        self.assertContains(page, "Showing: <b>Today,")

    def test_yesterday_a_picked_date_and_all_days(self, *_):
        owner, _x = self._three_days()
        today = timezone.localdate()
        self.assertEqual(self._names(owner, day="yesterday")[0], ["Yan"])
        three_ago = (today - timedelta(days=3)).isoformat()
        names, page = self._names(owner, day=three_ago)
        self.assertEqual(names, ["Dev"])
        self.assertEqual((page.context["day"], page.context["picked_date"]), ("date", three_ago))
        self.assertEqual(self._names(owner, day="all")[0], ["Dev", "Yan", "Tia"])    # oldest first
        # Picking today's or yesterday's date lights up those buttons, not the date box.
        self.assertEqual(self._names(owner, day=today.isoformat())[1].context["day"], "today")
        self.assertEqual(self._names(owner, day=(today - timedelta(days=1)).isoformat())[1].context["day"], "yesterday")

    def test_an_unreadable_day_falls_back_to_today(self, *_):
        owner, _x = self._three_days()
        for junk in ("abc", "2026-13-45", "tomorrow", "'; drop table"):
            self.assertEqual(self._names(owner, day=junk)[0], ["Tia"], junk)

    def test_cards_and_total_follow_the_chosen_day(self, *_):
        owner, by_name = self._three_days()
        yan = by_name["Yan"]
        a, b = yan.items.all()
        owner.post(f"/customer-requests/{yan.number}/", {
            "send": "1", f"available_{a.pk}": "on", f"price_{a.pk}": "90", f"available_{b.pk}": "on", f"price_{b.pk}": "45"})
        today_page = owner.get("/customer-requests/")
        self.assertEqual((today_page.context["total_count"], today_page.context["quoted_count"]), (1, 0))
        self.assertEqual(today_page.context["amounts"]["total"], Decimal("0"))
        yesterday_page = owner.get("/customer-requests/", {"day": "yesterday"})
        self.assertEqual((yesterday_page.context["total_count"], yesterday_page.context["quoted_count"]), (1, 1))
        self.assertEqual(yesterday_page.context["amounts"]["total"], Decimal("405.00"))
        all_page = owner.get("/customer-requests/", {"day": "all"})
        self.assertEqual(all_page.context["total_count"], 3)
        self.assertEqual(all_page.context["amounts"]["total"], Decimal("405.00"))

    def test_search_without_a_day_looks_at_all_days_but_respects_a_chosen_day(self, *_):
        owner, _x = self._three_days()
        names, page = self._names(owner, q="Dev")
        self.assertEqual(names, ["Dev"])                       # found although it was three days ago
        self.assertEqual(page.context["day"], "all")
        self.assertEqual(self._names(owner, q="Dev", day="today")[0], [])

    def test_buttons_and_cards_keep_the_other_filters(self, *_):
        owner, _x = self._three_days()
        page = owner.get("/customer-requests/", {"day": "yesterday", "q": "Yan", "status": "PENDING"})
        urls = page.context["urls"]
        self.assertIn("day=yesterday", urls["quoted"])
        self.assertIn("q=Yan", urls["quoted"])
        self.assertIn("status=PENDING", urls["today"])
        self.assertIn("q=Yan", urls["all_days"])
        self.assertNotIn("status=", urls["all"])               # the "All requests" card clears the status filter
        self.assertContains(page, 'max="' + timezone.localdate().isoformat() + '"')
        self.assertContains(page, 'name="day"')

    def test_an_empty_day_says_so_and_offers_all_days(self, *_):
        owner, _x = self._three_days()
        far = (timezone.localdate() - timedelta(days=30)).isoformat()
        page = owner.get("/customer-requests/", {"day": far})
        self.assertContains(page, "Nothing came in on")
        self.assertContains(page, "show all days")

    # ---- telling the owner ----

    def _cfg(self):
        return QuoteRequestSettings.objects.get(organization=self.shop)

    def test_a_new_request_emails_the_owner_with_the_list_and_a_link(self, *_):
        self._submit(address="12 Gandhi Road")
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["o@shop.example"])                      # the owner's login email by default
        self.assertIn("New customer request #1 from Ravi", msg.subject)
        for expected in ("Customer: Ravi", "Phone: 9876543210", "Address: 12 Gandhi Road",
                         "1. Colgate paste - 2 pc", "2. Sugar - 5 kg", "/customer-requests/1/"):
            self.assertIn(expected, msg.body)

    def test_emails_go_to_the_address_the_owner_chose(self, *_):
        QuoteRequestSettings.objects.filter(organization=self.shop).update(notify_email="orders@corner.example")
        self._submit()
        self.assertEqual(mail.outbox[0].to, ["orders@corner.example"])

    def test_emails_can_be_switched_off(self, *_):
        QuoteRequestSettings.objects.filter(organization=self.shop).update(notify_email_enabled=False)
        self._submit()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(QuoteRequest.objects.count(), 1)                 # the request itself is still saved

    def test_no_known_address_means_no_email_and_no_error(self, *_):
        User.objects.filter(pk=self.owner.pk).update(email="")
        self.assertEqual(self._submit().status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_changed_list_emails_the_owner_as_an_update(self, *_):
        self._submit()
        mail.outbox.clear()
        self._edit(QuoteRequest.objects.get())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Updated customer request #1", mail.outbox[0].subject)
        self.assertIn("1. Rice - 3 kg", mail.outbox[0].body)

    def test_a_broken_mail_server_never_breaks_the_customers_submit(self, *_):
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            resp = self._submit()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(QuoteRequest.objects.count(), 1)

    def test_owner_saves_notification_settings(self, *_):
        owner = Client()
        owner.force_login(self.owner)
        # The Notifications box is hidden on the inbox for now; the endpoint still works.
        self.assertNotContains(owner.get("/customer-requests/"), "Notifications")
        owner.post("/customer-requests/notifications/", {
            "notify_email_enabled": "on", "notify_email": "orders@corner.example", "notify_whatsapp": "98765 43210"})
        cfg = self._cfg()
        self.assertEqual((cfg.notify_email_enabled, cfg.notify_email, cfg.notify_whatsapp),
                         (True, "orders@corner.example", "98765 43210"))
        owner.post("/customer-requests/notifications/", {"notify_email": "", "notify_whatsapp": ""})   # box unticked
        cfg = self._cfg()
        self.assertEqual((cfg.notify_email_enabled, cfg.notify_email, cfg.notify_whatsapp), (False, "", ""))

    def test_bad_notification_settings_are_refused(self, *_):
        owner = Client()
        owner.force_login(self.owner)
        owner.post("/customer-requests/notifications/", {"notify_email_enabled": "on", "notify_email": "not-an-email"})
        owner.post("/customer-requests/notifications/", {"notify_email_enabled": "on", "notify_whatsapp": "12"})
        cfg = self._cfg()
        self.assertEqual((cfg.notify_email, cfg.notify_whatsapp), ("", ""))
        self.assertTrue(cfg.notify_email_enabled)

    def test_hospital_cannot_change_notification_settings(self, *_):
        c = Client()
        c.force_login(self.hosp_owner)
        self.assertEqual(c.post("/customer-requests/notifications/", {"notify_whatsapp": "9876543210"}).status_code, 403)

    def test_customer_gets_a_whatsapp_button_only_when_the_shop_set_a_number(self, *_):
        self._submit()
        req = QuoteRequest.objects.get()
        self.assertNotContains(self.anon.get(f"/r/{req.tracking_token}/"), "on WhatsApp")
        QuoteRequestSettings.objects.filter(organization=self.shop).update(notify_whatsapp="98765 43210")
        page = self.anon.get(f"/r/{req.tracking_token}/")
        self.assertContains(page, "Tell Corner-Shop on WhatsApp")
        url = page.context["shop_whatsapp_url"]
        self.assertTrue(url.startswith("https://wa.me/919876543210?text="), url)     # +91 added for an Indian shop
        for expected in ("order%20%231", "Colgate%20paste%20-%202%20pc", "Please%20check"):
            self.assertIn(expected, url)

    def test_the_whatsapp_button_goes_away_once_the_quote_is_sent(self, *_):
        QuoteRequestSettings.objects.filter(organization=self.shop).update(notify_whatsapp="9876543210")
        owner, req, *_x = self._quoted_request()
        self.assertNotContains(self.anon.get(f"/r/{req.tracking_token}/"), "on WhatsApp")

    def test_menu_badge_counts_requests_waiting_for_a_quote(self, *_):
        owner = Client()
        owner.force_login(self.owner)
        self.assertNotContains(owner.get("/customer-requests/"), 'title="Waiting for your quote"')
        self._submit()
        self._submit(name="Second")
        page = owner.get("/customer-requests/")
        self.assertContains(page, 'title="Waiting for your quote">2<')
        c = Client()
        c.force_login(self.hosp_owner)                                   # other business types never see it
        self.assertNotContains(c.get("/app/"), "Waiting for your quote")
