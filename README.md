# Business Analytics Intelligence

A multi-tenant SaaS that turns daily sales, expense and purchase entries into
business analytics — margins, trends, cash flow and full financial statements,
per organization.

Backend: Django + DRF
Frontend: Django templates + Tailwind (CDN) + Chart.js + vanilla JS
DB: PostgreSQL — **schema-per-tenant** multi-tenancy
Auth: Django session auth, email + password
PDF: xhtml2pdf for statement export

## Multi-tenancy model

- `public` schema (shared): `Organization`, `User` — account/identity data only.
- Every organization gets its **own PostgreSQL schema**, generated at signup:
  an 8-char random `organization_code` (e.g. `A1F93B2C`) becomes the schema
  name `org_a1f93b2c`. One account (one owner login) per organization.
- `apps.finance` (Revenue/Expense/Purchase entries, Categories, FinanceSettings)
  is the tenant-scoped app: its tables are migrated once into `public` as a
  template, then cloned into every organization's own schema at signup —
  see [`apps/organizations/tenant.py`](apps/organizations/tenant.py).
- `TenantSchemaMiddleware` ([`apps/organizations/middleware.py`](apps/organizations/middleware.py))
  points each request's DB connection at the logged-in user's organization
  schema, so `apps.finance` queries transparently read/write that
  organization's own isolated tables — no `organization` FK needed on any
  finance model, isolation is structural.
- Schema helpers live in [`apps/organizations/utils.py`](apps/organizations/utils.py);
  the signup transaction lives in [`apps/organizations/services.py`](apps/organizations/services.py).

## Project structure

```text
Business_analytics_Inteligence/
  apps/
    accounts/       # custom User model, login/signup views & forms
    organizations/  # Organization model, tenant schema/provisioning + signup service
    finance/        # tenant-scoped: entries, categories, dashboard, reports, PDFs
    core/           # landing page
  config/            # settings, urls
  templates/
  static/
  manage.py
  venv/
```

## Setup (Windows)

```powershell
cd D:\projects\Business_analytics_Inteligence
python -m venv venv
.\venv\Scripts\Activate
pip install -r requirements.txt
copy .env.example .env
```

Update `.env` with your local PostgreSQL credentials, then create the database
(`CREATE DATABASE bai;`) and run:

```powershell
python manage.py migrate
python manage.py provision_tenants   # backfill existing orgs (not needed for fresh installs)
python manage.py createsuperuser
python manage.py runserver
```

## Environments (dev vs prod)

A single `.env` file drives both environments via one flag. Only one line
should be uncommented at a time — comment out the one you're leaving and
uncomment the one you want active:

```dotenv
ENVIRONMENT=development
# ENVIRONMENT=production
```

`config/settings.py` uses `ENVIRONMENT` to pick which set of DB credentials to
connect with — `DEV_DB_*` when `development`, `PROD_DB_*` when `production` —
so both databases can be configured side by side in the same file and you
switch between them by changing one line:

```dotenv
DEV_DB_NAME=bai_dev
DEV_DB_USER=postgres
DEV_DB_PASSWORD=postgres
DEV_DB_HOST=localhost
DEV_DB_PORT=5432

PROD_DB_NAME=bai_prod
PROD_DB_USER=postgres
PROD_DB_PASSWORD=change-me-in-env
PROD_DB_HOST=localhost
PROD_DB_PORT=5432
```

`SECRET_KEY` follows the same split — `DEV_DJANGO_SECRET_KEY` has a
convenient default, `PROD_DJANGO_SECRET_KEY` has **no default** and Django
refuses to start in production without it. Generate a real one with:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## Production readiness

Everything below is driven entirely by `ENVIRONMENT` — there's no separate
settings module per environment, just one `config/settings.py` that reads
different values (or applies stricter defaults) when `ENVIRONMENT=production`:

- **`DEBUG`** — `True` in development, `False` in production (override with `DJANGO_DEBUG`).
- **`SECRET_KEY`** — dev default vs. required prod value (above).
- **SSL/cookies** — `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` all follow `DJANGO_SECURE_SSL` (defaults to "on in production, off in dev/tests"). Don't set this on a plain `runserver` — it expects a reverse proxy terminating TLS in front of it (`SECURE_PROXY_SSL_HEADER` is already configured for that).
- **HSTS** — `SECURE_HSTS_SECONDS` defaults to 1 hour once SSL is enabled, 0 otherwise; raise it with `DJANGO_HSTS_SECONDS` once HTTPS is confirmed solid (HSTS is sticky in browsers — don't set a long value before you're sure).
- **Static files** — production uses `CompressedManifestStaticFilesStorage` (hashed, cache-busted filenames); dev uses the plain compressed storage so `runserver` doesn't need a `collectstatic` pass on every change. **Run `python manage.py collectstatic` before deploying to production.**

Verify the full checklist any time with Django's own deploy check:

```powershell
python manage.py check --deploy
```

`ENVIRONMENT` also sets the default for `DEBUG` (`True` in development,
`False` in production) — override it explicitly with `DJANGO_DEBUG` if
needed. See [`.env.example`](.env.example) for the full template.

## Superadmin login

The platform superadmin's credentials live in `.env`
(`SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`), not a password set ad hoc via
`createsuperuser`. Provision or update that user with:

```powershell
python manage.py bootstrap_superadmin
```

Re-run it any time you change either value in `.env` — it's idempotent and
syncs the existing user (email, password, `role=SUPER_ADMIN`, `is_staff`,
`is_superuser`) to match. Log in at `/accounts/login/` with those
credentials.

## Pages

- Landing: `/`
- Sign up (creates an organization + its isolated schema): `/accounts/signup/`
- Sign in: `/accounts/login/`
- Dashboard (charts, KPIs, quick-add sales/expenses/purchases): `/app/`
- Daily entries (browse/delete): `/app/entries/`
- Financial reports — P&L, Balance Sheet, Cash Flow, with PDF export: `/app/reports/`
- Finance settings (financial-year start month, opening balance, categories): `/app/settings/`
- Django admin: `/admin/`

## Financial statements

Reports honor a period picker (today / week / month / quarter / financial
year / custom range), with the financial year start month configurable per
organization in Settings. The Balance Sheet is a **simplified, cash-basis**
statement: it assumes the business started with opening capital equal to its
opening cash balance and tracks no separate inventory, receivables or
payables — appropriate for a business logging simple daily sales, expenses
and purchases rather than running full double-entry bookkeeping. All three
statements (P&L, Balance Sheet, Cash Flow) can be downloaded as PDF.

## Notes

- Tailwind and Chart.js are loaded via CDN to keep setup simple.
- New organizations are provisioned automatically at signup. Run
  `manage.py provision_tenants` once after adding new finance models/fields,
  or when bringing an older organization up to date.
