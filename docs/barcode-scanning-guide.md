# Barcode scanning, labels and phone stock: how to run

This guide covers the Scan & Bill feature for sales and purchases: scanning items, printing barcode labels, tracking phones by IMEI, and using it on the web and in the Android app.

## 1. Organization codes: which features each kind of organization gets

A superadmin gives every kind of organization a short **code**, and the code decides its extra features. Only a superadmin sees or changes codes. They are never shown to an organization, in its pages or in the phone app.

| Code | Kind | Scanning, inventory & labels | Inventory type |
|---|---|---|---|
| `HOSP` | Hospitals | Off | Others |
| `DIAG` | Diagnostic centres | Off | Others |
| `REST` | Restaurants | Off | Others |
| `MOBS` | Mobile shops | On | Mobiles (each phone by IMEI, with brand, colour, RAM, storage) |
| `CLTH` | Clothing stores | On | Clothes (brand, size, colour) |
| `GENR` | General stores | On | Others (plain items and quantities) |

- **Manage the codes:** superadmin → **Org codes** (in the sidebar, under Customers). Add a code for any other kind of organization, and choose what it turns on.
- **Give a client its code:** open the client, then use **Organization code** → **Assign code**. This sets the client's scanning switch and inventory type to the code's.
- **Change a code later:** saving a code applies its features to every client that has it.
- **Removing a client's code** leaves its features as they are.
- Existing hospitals, diagnostic centres and restaurants were filed under HOSP, DIAG and REST when this was added. Their features were not changed.
- The code is separate from the client's billing plan, which controls pricing and pages.

## 1a. Category structure: how many levels, and what they are called

By default an organization has **3 levels**: Category > Sub-category > Item. A superadmin can set from **2 to 8 levels**, name each one, and mark some as **common**.

- **Normal levels** form a tree. The last normal level is the item itself. The ones above it are groups (for a phone shop: Mobile > Brand, with the item being the Model name).
- **Common levels** are shared lists that sit beside the tree, such as Colour, RAM and Storage. They are optional on every item. A value added once (say `Black`) is offered to every group and every item.
- Every structure needs at least 2 normal levels. Names must be different from each other.

| Code | Structure |
|---|---|
| default, `GENR` | Category > Sub-category > Item |
| `MOBS` | Mobile > Brand > Model name, plus common Colour, RAM, Storage |
| `CLTH` | Category > Sub-category > Item, plus common Brand, Size, Colour |

- **Set it for a kind of organization:** superadmin → **Org codes** → edit the code → **Category structure**. Saving applies it to every client with that code.
- **Set it for one client:** open the client → **Category structure**. A later change to the code applies to that client again.
- Changing the structure later does not delete anything. Items keep the groups and common values they already have.
- The structure is used on the scan page (new item and edit item), on the Setup page inventory list, and in the phone app (new item and edit item sheets). Common lists can be added to and removed from on the Setup page.
- Only a superadmin sees or changes the structure. It is not shown to organizations as a setting.

## 2. Turn it on for one client only (override)

The code sets a client's features. To change them for just one client, use the switch on the client's page. A later change to the code applies to that client again.

Barcode scanning is off by default for every organization.

- Sign in as superadmin, open the organization, and use **Barcode scanning (Scan & Bill)** → **Turn on**.
- Next to the switch, pick the client's **inventory type**. It decides what is recorded for each item:

| Type | What it records |
|---|---|
| **Mobiles** | Each phone is tracked by its IMEI. An item carries brand, colour, RAM and storage, and each phone also has a model, second IMEI and notes. |
| **Clothes** | An item carries brand, size, colour and notes. Stock is counted by quantity. No IMEI tracking. |
| **Others** | Plain items with a brand and notes. Stock is counted by quantity. |

- The client list shows a **Scan on** tag beside the storage tag for every client that has it.
- One switch controls all of these for that organization, on the web and in the phone app:
  - Scan & Bill (sale and purchase), the camera scanner, and the camera button
  - Inventory & stock, and the phone (IMEI) stock list
  - Barcode labels
  - Item search, item editing, and phone details
- Until it is on, none of it shows. The scan pages return 404, the buttons and links are hidden, and the scan endpoints return `feature_disabled`. The phone app is not even sent the sub-category list for it.
- Each organization has its own items, phones and bills. An organization never sees another's, whether or not both are switched on.
- Organizations that keep their data in Google Sheets only get the extra `Product` and `StockUnit` tabs once the switch is turned on for them. Until then those tabs are not created or read.
- The phone app reads the setting when it loads, so a change shows after the app is reopened.

## 3. Run the backend (Django)

```bash
cd backend
./venv/Scripts/python.exe manage.py migrate          # apply pending migrations
./venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000
```

Migrations added by this feature:

| App | Migration |
|---|---|
| finance | `0044_product_scan_and_bill` (products, bill number on entries) |
| finance | `0045_barcode_codes_for_categories` (codes on categories and sub-categories) |
| finance | `0046_stock_units` (phones tracked by IMEI) |
| finance | `0047_product_subcategory` (item → sub-category link) |
| finance | `0048_stock_unit_details` (brand, model, colour, RAM, storage, second IMEI, other details on each phone) |
| finance | `0049_inventory_type` (item brand, size, colour, other details) |
| finance | `0050_item_details_and_opening_stock` (item RAM and storage, opening quantity) |
| finance | `0051_common_options_and_values` (shared lists for common levels, common values on items) |
| organizations | `0026_barcode_scanning_enabled` (the on/off switch) |
| organizations | `0027_inventory_type` (Mobiles, Clothes or Others, set by a superadmin) |
| organizations | `0028_organization_categories` (organization codes) |
| organizations | `0029_seed_organization_categories` (the starter codes HOSP, DIAG, REST, MOBS, CLTH, GENR) |
| organizations | `0030_category_levels` (the category structure on codes and clients) |
| organizations | `0031_preset_category_levels` (Mobile > Brand > Model structure for MOBS, Clothes for CLTH) |

Every environment (dev, production) needs `migrate` run once.

Run the tests:

```bash
cd backend
./venv/Scripts/python.exe manage.py test apps.finance.test_scan_billing apps.mobile_api
```

## 4. Use it on the web

Sign in, then open the Daily Book (sidebar **Daily Book**).

| Page | Address | What it does |
|---|---|---|
| Scan & Bill (sale) | `/app/daily/scan-bill/?type=sale` | Scan, search by name, bill, save |
| Scan & Bill (purchase) | `/app/daily/scan-bill/?type=purchase` | Record purchases and phone IMEIs |
| Scan with camera | `/app/daily/scan-bill/?camera=1` | Opens the camera straight away |
| Barcode labels | `/app/daily/scan-bill/labels/` | Print labels for items, categories and sub-categories |
| Inventory & stock | `/app/daily/scan-bill/inventory/` | Bought, sold and in stock per item; phones by IMEI; recent purchases and sales |
| Setup | `/app/setup/` | The **Inventory** section lists every item with all its fields editable, adds items (price, opening quantity, brand, colour, RAM, storage or size), and, for mobiles, adds phones already in stock by IMEI |
| Setup categories | `/app/setup/` (Revenue, Purchase and Product category cards) | Create categories and sub-categories |

- **Sale:** scan an item's barcode, or scan a phone's IMEI (either IMEI of a dual-SIM phone works). Each phone is one line, and it shows what the phone was recorded as.
- **Purchase:** scan the model's box barcode, then scan each phone's IMEI. Use **Phone details** on each phone to record its brand, model, colour, RAM, storage, second IMEI (dual SIM) and any other details. Brand and model start from the item, and colour, RAM and storage carry over from the last phone of the same model, so a batch is quick to enter.
- **One item per variant:** an item is one barcode, for example *iPhone 15, Black, 6GB, 128GB* with its own box barcode. Enter its brand, colour, RAM and storage once. After that, scanning its barcode on a purchase fills them in, and only the IMEI is scanned for each phone. An IMEI belongs to one physical phone, so it can't come from a search.
- **Opening stock:** for items not tracked by IMEI, set the **opening quantity** on the Setup page. In stock = opening + bought − sold.
- **Items:** use **Edit item** on a bill line to change the name, barcode, prices and sub-category.
- **Labels:** each category and sub-category has **Print one label** and **Print full sheet** (24 copies, one A4 page).
- **Inventory & stock:** three tabs. *Stock by item* shows bought, sold, in stock and stock value for every item. *Phones by IMEI* lists each phone with its details (brand, model, colour, RAM, storage, second IMEI, notes) as in stock or sold, with filters. Search matches the IMEI, model, brand, colour, RAM and storage. *Purchases & sales* shows the last 90 days. A phone model counts its phones by IMEI; any other item is bought minus sold.

## 5. Run the Android app

Requirements: Flutter (stable), the Android SDK, and a phone on the same Wi-Fi as this PC.

1. Find this PC's Wi-Fi address with `ipconfig` (for example `10.155.196.76`).
2. Allow that address in `backend/.env` (`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`).
3. Start the server on all addresses:

   ```bash
   cd backend
   ./venv/Scripts/python.exe manage.py runserver 0.0.0.0:8000
   ```

4. Build the app for that address:

   ```bash
   cd mobile
   flutter build apk --debug --dart-define=API_BASE_URL=http://10.155.196.76:8000/api/mobile/
   ```

5. Install `mobile/build/app/outputs/flutter-apk/app-debug.apk` on the phone, or run `flutter run` with the same `--dart-define` while the phone is connected over USB with USB debugging on.

Check the analyzer and tests:

```bash
cd mobile
flutter analyze lib/features/scan_bill lib/shared lib/app
```

Use the **camera button** (bottom right, shown when scanning is on) to open the scanner in Sale mode. Switch to **Purchase** at the top of the screen to buy. The app bar has **Inventory & stock** and **Print barcode labels**. The Daily screen also has an **Inventory & stock** icon. The inventory screen has an *Items* tab (bought, sold and in stock per item) and a *Phones (IMEI)* tab (each phone, in stock or sold).

## 6. Where the code is

**Backend** (`backend/apps/`)

| File | Purpose |
|---|---|
| `finance/models.py` | `Product`, `StockUnit`, and the barcode fields on categories and sub-categories |
| `finance/scan_billing.py` | Rules: codes, resolving a scan, saving bills, phone tracking, product edits |
| `finance/scan_views.py` | Web pages and JSON endpoints for scanning and labels |
| `finance/barcode_labels.py` | Builds the printable A4 label sheets (PDF) |
| `finance/inventory.py` | Inventory totals, recent purchases and sales, and the phone list |
| `finance/test_scan_billing.py` | Tests for scanning, labels, phones and sub-categories |
| `mobile_api/views.py`, `urls.py` | Endpoints the phone app calls (`/api/mobile/...`) |
| `mobile_api/test_labels.py`, `test_stock.py` | Tests for the phone endpoints |
| `superadmin/views.py` | The on/off switch for each organization, and the Org codes pages |
| `organizations/models.py`, `services.py` | `OrganizationCategory` (the codes), and applying a code to a client |
| `organizations/test_org_codes.py`, `superadmin/test_org_codes.py` | Tests, including that organizations never see their code |

**Web templates** (`backend/templates/finance/`)

- `scan_bill.html`: the scan page
- `barcode_labels.html`: the label page
- `inventory.html`: the inventory and stock page
- `daily_report.html`: the Scan items button on the Daily Book

**Phone app** (`mobile/lib/`)

| File | Purpose |
|---|---|
| `features/scan_bill/presentation/scan_bill_screen.dart` | Scan screen: camera, search, bill, item editor |
| `features/scan_bill/presentation/stock_screen.dart` | Inventory & stock: items and phones |
| `features/scan_bill/presentation/full_screen_scanner.dart` | The full-screen camera |
| `features/scan_bill/presentation/barcode_labels_screen.dart` | Label printing |
| `features/scan_bill/data/` | Calls to the server |
| `features/scan_bill/models/` | Product, bill line, stock and lookup models |
| `shared/widgets/main_shell.dart` | The camera button |
| `app/router.dart` | Screen routes (`/scan-bill`, `/phone-stock`, `/barcode-labels`) |

## 7. Known gaps

- **IMEI import:** loading a list of IMEIs from a file is not built yet. Phones are entered by scanning.
- **Phone-by-phone checks:** the Flutter screens have not been run on a device. Test one purchase and one sale before relying on them.
