# Finday Mobile

Flutter app (Android / iOS) for recording **revenue, expense and purchase entries**
on the go and reading the same reports as the web app (dashboard, intelligence, monthly
summary, financial reports, cash position, ledgers). It talks to the Django backend through `backend/apps/mobile_api`
(`/api/mobile/...`), which reuses the web app's entry forms, so validation, plan
limits and per-organization data isolation are identical to the website.

Structure and stack follow the `mithrogo_rideplatform/customer_app` reference:
Riverpod (state), go_router (navigation), Dio (HTTP), flutter_secure_storage (token).

## Screens

Every report screen follows one layout, described in [docs/REPORT_BLUEPRINT.md](docs/REPORT_BLUEPRINT.md):
pill tabs, then labelled dropdown filters, then a headline card, then detail sections.

- **Splash** — animated brand screen while the saved sign-in is checked (at least 1.8 s). If the server can't be reached it offers "Try again" instead of signing you out.
- **Login** — same email/username + password as the web app.
- **Dashboard** — this month's numbers, last 7 days and top categories.
- **New entry** — Revenue / Expense / Purchase pills, a date strip (Today / Yesterday shortcuts), searchable pickers for category, sub-category and item (cascading, as on the web), a large amount field, quantity and discount (with a live net), vendor, payment mode (+ bank account), note, and the day's entries. Pull down to refresh.
- **Intelligence** — Range dropdown (presets or a custom date range) and Analytics / Revenue / Cost tabs. Revenue and cost list each category with its **quantity**. The Revenue tab also has a quantity chart, insights and a details table; tapping a category opens the **quantity breakdown** (by sub-category, item and day).
- **Monthly summary** — Revenue / Expense / Purchase tabs, "View by" (daily to yearly) and Range dropdowns, a same-range-last-year card, and an expandable card per category with its sub-categories and a per-period list with ▲/▼ change.
- **Financial reports** — P&L, Balance, Cash and GST tabs with a Range dropdown. The Cash tab has a **Show** dropdown (Cash + online / Cash only / Online only) and always labels which one you are viewing; single-mode views also list transfers between cash and bank.
- **Category statement** — pick a type, category, sub-category and date range to see every entry (with quantity) behind a total. Reached from Intelligence and More.
- **Category, day and quantity details** — drill-downs from the reports above.
- **Billing** — current plan and status, what is outstanding, price breakdown, invoices and payments. Read-only: when something is outstanding the app shows a link to pay on the web (with a Copy link button). Owner accounts only; a lapsed plan can still open it.
- **Daily performance, daily book, cash position, bank accounts, vendors, receivables and payables, ledgers** — under More.

Categories, banks and vendors are managed in the web app; the phone only reads them.

## Folder structure

```text
lib/
  main.dart                  # ProviderScope + App
  app/                       # app-wide wiring
    app.dart                 #   MaterialApp.router
    router.dart              #   routes + sign-in redirects
    providers.dart           #   composition root (api client, repositories)
    config/env.dart          #   API URL via --dart-define
    theme/                   #   colors, ThemeData
  core/                      # no feature knowledge
    constants/app_constants.dart
    network/                 #   api_client, api_endpoints, api_exception
    storage/token_storage.dart
    utils/date_formatter.dart
  features/                  # one folder per feature: data / application / models / presentation
    splash/presentation/
    auth/                    #   session_controller, auth_repository, AppUser, login_screen
    entries/                 #   entries_repository, providers, models, entry_screen + widgets/ (entry_form_widgets: cards, pickers, date strip)
    dashboard/ daily/ intelligence/ more/ reports/   # report screens, providers and the reports repository
  shared/widgets/            # reusable UI (AppButton, ErrorView, report_widgets, report_blueprint)
test/                        # unit tests
```

Conventions: a feature owns its screens, models, repository and providers; features
share code only through `core/` and `shared/`. New endpoint -> `core/network/api_endpoints.dart`
+ a repository method. New screen -> `features/<name>/presentation/` + a route in `app/router.dart`.

## Run it

```bash
cd mobile
flutter pub get
flutter run
```

### Point the app at the backend

`API_BASE_URL` defaults to `http://10.0.2.2:8000/api/mobile/` (how the Android emulator reaches your PC).

| Where you run the app | `API_BASE_URL` |
| --- | --- |
| Android emulator | `http://10.0.2.2:8000/api/mobile/` (default) |
| iOS simulator / desktop | `http://127.0.0.1:8000/api/mobile/` |
| Real phone on the same Wi-Fi | `http://<your-PC-LAN-IP>:8000/api/mobile/` |
| Deployed site | `https://your-domain/api/mobile/` |

```bash
flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000/api/mobile/
```

Backend side:

1. From `backend/`, run Django on all interfaces: `python manage.py runserver 0.0.0.0:8000`
2. Add the host to `DJANGO_ALLOWED_HOSTS` in `backend/.env` (e.g. `localhost,127.0.0.1,10.0.2.2,192.168.1.20`).

Debug builds allow plain `http`; release builds are https-only.

### iOS

The same code runs on iOS (`ios/` is set up: bundle id `com.finday.findayMobile`, iOS 15+). Building needs a Mac with Xcode:

```bash
cd mobile
flutter pub get
cd ios && pod install && cd ..
flutter run -d <your iPhone or simulator> --dart-define=API_BASE_URL=http://<Mac-LAN-IP>:8000/api/mobile/
```

For a device or the App Store, open `ios/Runner.xcworkspace` in Xcode, pick your Team under Signing & Capabilities, and change the bundle id if `com.finday.findayMobile` is taken. `Info.plist` allows plain `http` only to local-network hosts (development).

## Checks

```bash
flutter analyze
flutter test
```

## API (all JSON, `Authorization: Bearer <token>`)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/mobile/login/` | `{email, password}` -> `{token, user, organization}` |
| GET | `/api/mobile/me/` | `{user, organization}` for the token; used to restore a session |
| GET | `/api/mobile/entry-options/` | categories per type with sub-categories and their items (`children`), payment modes, banks, vendors |
| GET | `/api/mobile/entries/?date=YYYY-MM-DD` | that day's entries |
| POST | `/api/mobile/entries/` | `{type: sale\|expense\|purchase, date, category, subcategory? (the most specific one chosen), amount (gross_amount for sale), quantity? (sale, purchase), discount?, vendor?, payment_mode, bank_account?, note?}` |
| GET | `/api/mobile/dashboard/` | month totals, last 7 days, top categories |
| GET | `/api/mobile/daily-performance/?from=&to=` | day-by-day totals for a range |
| GET | `/api/mobile/intelligence/?period=` (`period=custom&from=&to=` for a custom range; same for monthly-summary and financial-reports) | KPIs, trends, and revenue / expense / purchase by category (revenue and purchases include `quantity`) |
| GET | `/api/mobile/monthly-summary/?kind=sale\|expense\|purchase&granularity=&period=` | category-by-period matrix with sub-categories, change %, and year-over-year; also the legacy `months` list |
| GET | `/api/mobile/financial-reports/?period=&flow=all\|cash\|bank` | P&L, balance sheet, cash flow (`flow` narrows the cash flow to cash, online or both), GST |
| GET | `/api/mobile/cash-position/`, `bank-accounts/`, `vendors/`, `aging/` | balances, receivables and payables |
| GET | `/api/mobile/day-detail/?date=` | everything recorded on a day, with opening/closing cash and bank |
| GET | `/api/mobile/category-detail/?kind=&name=&from=&to=&subcategory=` | entries of a category (with `quantity`, `total_quantity`) |
| GET | `/api/mobile/ledger/?kind=bank\|vendor&id=` | running-balance ledger |
| GET | `/api/mobile/billing/` | plan, subscription, outstanding amount, invoices, payments, web pay link (works while the plan is lapsed; managers get `403`) |

Errors: `401` sign in again, `403` suspended / plan lapsed / manager without Daily Book access, `400` field errors under `fields`.
Tokens last 30 days and stop working when the user's password changes.

## Not in the app yet

Editing or deleting entries, offline queueing, push notifications, exports, a custom launch/app icon.
