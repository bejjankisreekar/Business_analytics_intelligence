# Customer Requests (open link, quote, pay) — plug-in

Optional add-on for **shop and restaurant** organizations (Retail & E-commerce, Restaurants & Food).
Hospitals and every other business type never see it. Code lives in `backend/apps/quote_requests/`.

## What it does

1. The owner turns on an **open link** (`/request/<org-slug>/`) and prints its **QR code** for the counter.
2. A customer enters name, mobile and an optional delivery address, then a list: **item, quantity, unit** (kg, grams, pc, dozen, litre, ml,
   packet, box, bottle, bag, plate, portion, metre). Item names suggest from the shop's inventory
   (product categories and subcategories).
3. They get a private page (`/r/<token>/`). Until the quote is sent they can **edit their list**.
4. The owner opens the request, marks each item available or not, enters a **rate per unit** and a remark,
   and sends the quote (rate x quantity per row, with a total).
5. The customer sees prices and the total, and can download the quotation **PDF**. The owner can share the PDF on **WhatsApp**.
6. The owner marks the request **Paid** (Cash or Online) by hand. The amount is booked in the **Daily Book** as one
   lump sum under the revenue channel **"Customer requests"** (shown as "Total Customer requests"). Marking it
   pending again, or changing the amount after payment, removes that sale.

There is no payment gateway; payment is recorded manually by the owner.

## Telling the owner

- **Email** to the owner (or the address they choose) for every new request and every edit before the quote.
  Controlled from the Customer Requests page (Notifications).
- **WhatsApp number**: if the owner saves one, customers see a button after sending their list that opens a
  ready-made WhatsApp message to the shop.
- **Menu badge**: the Customer Requests menu item shows how many requests still wait for a quote.
- **Mobile app**: a **Requests** tab (list by day, search, quote, mark paid) and **push notifications**. Push
  needs a one-time Firebase setup: see `mobile/docs/push-notifications.md`. Server variables:
  `FCM_SERVICE_ACCOUNT_JSON` or `FCM_SERVICE_ACCOUNT_FILE`.

## Page access (superadmin only)

A superadmin can limit an organization to chosen pages: **Clients -> Edit -> Page access**. Tick pages to limit all
of that client's logins to them; tick nothing for full access (the default). No pricing or plan is involved.
The keys are in `apps/accounts/manager_pages.py` (`ORG_PAGE_CHOICES`); Customer Requests has the key
`customer_requests`.

## Deploying to production

**Before the new code serves traffic, run migrations** (nothing in the Dockerfile or cloudbuild does this for you):

```bash
python manage.py migrate
```

This adds `organizations.0023_org_allowed_pages` and `quote_requests.0001` to `0011`. Both only add tables or a
column with a default, so existing organizations are unaffected.

Checklist:

- [ ] `ENVIRONMENT=production`, so QR codes and shared links use `SITE_URL` (`https://findayintelligence.com`).
      Anywhere else they follow the address the page was opened with, which keeps phone testing on Wi-Fi working.
- [ ] `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` include `findayintelligence.com` and `www.findayintelligence.com`.
- [ ] On the production host do **not** set `DJANGO_SECURE_SSL=False` or `DJANGO_HSTS_SECONDS=0`
      (those are local-development overrides in `.env`).
- [ ] Open `https://findayintelligence.com/customer-requests/qr/`, scan the QR with a phone and confirm it opens the request page.
- [ ] Phones only attach the real PDF file in the WhatsApp share sheet over **https**; on a computer it opens the chat with a link.
- [ ] No new Python packages are needed (QR uses reportlab and PDFs use xhtml2pdf, both already in `requirements.txt`).

Notes:

- The open request form is limited to 10 submissions an hour per IP. The counter lives in each server process's memory,
  so with several gunicorn workers or Cloud Run instances the real limit is per process. Add a shared cache
  (`CACHES`) if you need a hard global limit.
- Existing paid requests created before the Daily Book link have no sale booked; none exist in production yet.
- Demo data created while building this exists only in the development database.

## Tests

```bash
python manage.py test apps.quote_requests apps.accounts.test_org_page_limit
```
