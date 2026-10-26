# Finday Intelligence: Project Documentation

Prepared: 12 October 2026
Scope: the Finday web application in this repository (`Business_analytics_intelligence`)

---

## 1. Overview

Finday turns a business's daily sales, purchase and expense entries into profit, ledger and cash insights. It is built for Indian businesses (hospitals, clinics, retail, restaurants, wholesale and similar), with INR currency, GST reporting and Indian financial-year defaults.

- Log daily sales, purchases and expenses in seconds, or import a spreadsheet.
- See margins, revenue and cost drivers on a live dashboard.
- Keep ledgers for customers, vendors, partners and bank accounts.
- Produce P&L, Balance Sheet, Cash Flow and GST summary reports, exportable as PDF, Excel or CSV.
- Track referral (kickback) commissions due and paid per referrer.

---

## 2. Key features

| Module | What it does |
|---|---|
| Dashboard | KPIs, daily, weekly and monthly trends, weekday averages, revenue and cost breakdowns, year-over-year comparison |
| Daily entries | Sales, expenses and purchases with payment mode, category and subcategory, quantity and discount |
| Bulk import | Load entries from a spreadsheet, with configurable column labels |
| Receivables and payables | What customers owe you and what you owe vendors, with aging |
| Customers and vendors | Master lists with balances and vendor breakdowns |
| Bank accounts and cash transfers | Track balances across cash and bank accounts |
| Partners | Partner transactions and a per-partner ledger |
| Referral commissions | Commission entries per referrer, with payouts due and paid |
| General ledger | Ledger entries and a general-ledger view |
| Reports | P&L, Balance Sheet (simplified, cash basis), Cash Flow, GST summary; period picker (today, week, month, quarter, financial year, custom range). Cash Flow can be viewed for cash and online together, cash only or online only |
| Mobile app | Flutter app for Android and iOS: enter revenue, expenses and purchases (category, sub-category, item, quantity, discount) and read the dashboard, intelligence (with quantity by category), monthly summary, financial reports, category statements, cash position and ledgers. It uses the same server and access rules as the web app |
| Exports | PDF for statements; Excel and CSV for data |
| Settings | Financial-year start month, categories and subcategories, column labels |

---

## 3. Users and roles

| Role | Who it is for | Notes |
|---|---|---|
| Super Admin | Finday platform operator | Manages organizations, plans, service status and storage-change approvals |
| Organization Owner | The business owner who signs up | One owner login per organization |
| Admin | Additional administrator | Available on plans with two logins |
| Manager | Staff with restricted access | Can be limited to selected pages |
| Member | Other team members | Team access within the organization |

The number of manager logins is set by the plan: one Admin account, or one Admin plus one manager account.

---

## 4. Architecture

**Each client chooses where its finance data is stored.**

```
 Browser (phone or laptop)
        |
        v
 Finday web app (Django)  <------->  Razorpay (subscriptions, payments)
 Login, pages, access rules
        |
        v
 Finance engine
 Entries, reports, PDFs
        |                              |
        | Sheets API                   | or managed storage
        v                              v
 Client's own Google Sheet      Finday managed database
 Default: in the client's       PostgreSQL, separated
 Google Drive                   per organization
```

- **Google Sheet (default).** The organization's finance data lives in a Google Sheet in its own Google Drive. Finday connects with Google OAuth and only uses the `drive.file` permission, which limits access to files Finday creates or the user opens with it. Sheet data is read and written live; it is not copied into Finday's database.
- **Managed database.** Organizations that prefer not to use Google Drive store data in Finday's PostgreSQL database. Each organization's records are kept separate by an organization field on every record.
- **Technology.** Python and Django, Django REST Framework, PostgreSQL, Django templates with Tailwind CSS and Chart.js, xhtml2pdf for PDF statements, and Gunicorn with WhiteNoise for deployment.

---

## 5. Data storage and security

| Area | How it is handled |
|---|---|
| Data location | The client's own Google Drive (default), or Finday's managed database |
| Isolation | Each organization's data is separated. In the managed database, every record carries an organization field. |
| Google access | OAuth with the `drive.file` scope only; tokens are encrypted at rest with Fernet |
| Connection | HTTPS is enforced in production, with secure cookies and HSTS |
| Sign-in | Email and password, with Django's password validators |
| Password recovery | One-time codes (OTP) sent by email |
| Sessions | Sessions expire after 12 hours by default |
| Access control | Role-based access; managers can be limited to selected pages |
| Storage changes | Moving between Google Drive and the managed database requires a request and superadmin approval, with the billing impact shown first |
| Reports | Statements can be downloaded as PDF at any time |
| Revoking access | The client can remove Finday's access from its Google account at any time |

---

## 6. Billing and plans

Payments are processed through Razorpay, including subscription autopay and payment webhooks. The plan catalogue below reflects the values in the code's plan-sync migration (October 2026) and should be confirmed before it is quoted.

| Plan (display name) | Monthly (INR) | Yearly (INR) | Logins | Storage |
|---|---|---|---|---|
| Professional | 1,999 | 23,988 | 1 | Finday managed database |
| Professional Drive | 2,499 | 29,988 | 1 | Client's Google Drive |
| Business | 2,499 | 29,988 | 2 | Google Drive |
| Business Drive | 2,999 | 35,988 | 2 | Google Drive |

Other features of billing:

- Trial period: 14 days in the current configuration.
- Percentage discounts on monthly and yearly prices, shown on the landing page.
- Coupons and coupon redemptions.
- Invoices and payment records, with renewal invoices generated by a scheduled command.

---

## 7. Market comparison

### Why this comparison matters

Many free or low-cost tools cover part of this need. The table compares what each typically offers, based on public pricing pages and third-party reviews checked in October 2026. Prices change often; each row should be checked on the vendor's site before a decision.

| Option | Typical cost (checked Oct 2026) | Strength | Where it falls short for daily business tracking |
|---|---|---|---|
| Google Sheets or Excel with templates | Free | Flexible, familiar, no setup cost | No automation, no guided entry, manual reports, error-prone as data grows, no login per staff member |
| Zoho Books | Free plan for small businesses in some regions; paid tiers from about USD 20 a month (US) | Full accounting, invoicing, bank feeds | Built for invoicing and accounting, not daily cash-style entry; free tier has user, invoice and branding limits |
| Wave Accounting | Free core tier; bank connections and multi-user access require Pro (about USD 16 to 19 a month) | Free invoicing and bookkeeping | Automatic bank feeds and shared access moved to paid Pro in June 2026; US and Canada focus |
| GnuCash | Free and open source | Full double-entry accounting, offline | Desktop only, no hosted login for staff, manual bank imports, steep learning curve |
| myBillBook | Free plan; paid plans from about INR 399 a year (vendor's entry price) | Indian GST billing, inventory, mobile | Billing and inventory focus rather than profit, ledger and cash analytics |
| Vyapar | Limited free plan; paid desktop and mobile plans from about INR 942 to 3,999 a year (third-party figures) | Offline billing, GST, mobile | Billing focus; advanced features behind higher tiers |
| Khatabook | Not confirmed (no reliable current price found) | Customer credit (udhaar) tracking, simple mobile use | Focused on credit tracking, not full P&L, Balance Sheet or Cash Flow |
| Tally Prime | Silver single-user list price about INR 22,500 plus GST, plus about INR 4,500 a year for updates (third-party figures) | Industry standard for Indian accountants | Desktop software, needs accountant knowledge, not designed for daily owner dashboards |

### How Finday differs

1. **Built for daily owner entries.** Sales, purchases and expenses are logged quickly, and the dashboard updates from those entries rather than requiring invoices first.
2. **Data stays with the client.** The default storage is the client's own Google Drive. Finday does not keep a copy of it. Clients can read, export or delete it directly, and can revoke Finday's access at any time.
3. **Reports for the accountant.** P&L, Balance Sheet, Cash Flow and GST summary come from the same entries, so the accountant gets consistent figures without re-keying.
4. **Multi-user access with role limits.** Owners and managers have separate logins, with page-level limits for staff.
5. **Indian defaults.** INR, GST, Indian financial-year months, Indian digit grouping in amounts.
6. **Referral commissions included.** Commission tracking per referrer is built in, which general accounting tools do not provide.

### Honest trade-offs

- Finday is not a full double-entry accounting package. The Balance Sheet is simplified and cash-based, which suits daily business tracking but not statutory audit or complex accounting.
- Invoicing and e-way bills are not part of the core product (not verified in the codebase; to be confirmed).
- Finday depends on a working internet connection and a Google account for the default storage option. Offline-first tools such as Vyapar and GnuCash work without one.
- Accountants familiar with Tally may need a short onboarding to use Finday's reports.

---

## 8. Deployment and operations

- Environment is controlled by one `.env` file: `ENVIRONMENT=development` or `production`.
- Deployment uses Docker and Google Cloud Build (see `Dockerfile` and `cloudbuild.yaml`).
- Scheduled and management commands include renewal invoice generation, plan sync between environments and superadmin bootstrap.
- Production checks: `python manage.py check --deploy`.

---

## 9. Open questions for the client

1. Confirm the exact plan names, prices and included logins before sharing with customers.
2. Confirm the per-role permission matrix (Owner, Admin, Manager, Member).
3. Confirm whether invoicing or GST return filing is in scope.
4. Confirm the target customer list, since the marketing copy and the code differ slightly in role names.

---

## 10. Sources

Market information (checked October 2026; third-party sources, verify before publishing):

- Zoho Books free plan and pricing: https://costbench.com/software/accounting/zoho-books/free-plan ; https://rounded.com.au/blog/2026-zoho-books-pricing-explained ; https://www.patronaccounting.com/blog/zoho-books-pricing-india-2026
- Wave Accounting plan changes: https://support.waveapps.com/hc/en-us/articles/26449863006612 ; https://beancount.io/blog/2026/08/05/wave-accounting-bank-feeds-collaborator-paywall-guide
- GnuCash releases: https://gnucash.org (news page; 5.16 announced June 2026)
- myBillBook pricing: https://www.capterra.com/p/202732/FloBooks/pricing/ ; https://mybillbook.in/blog/tally-prime-vs-marg-erp/
- Vyapar comparison: https://accountune.com/vyapar-vs-accountune-vs-mybillbook ; https://www.gimbooks.com/blog/best-billing-apps-for-android-in-india/amp/
- Tally Prime pricing: https://tallysolutions.com/business-guides/tallyprime-total-cost-of-ownership-india/ ; https://www.erpresearch.com/pricing/tallyprime

Khatabook: no reliable current pricing was found; check the vendor's website before comparing.

Finday facts: taken from this repository's code, models, migrations and marketing assets, as of the repository state on 12 October 2026.
