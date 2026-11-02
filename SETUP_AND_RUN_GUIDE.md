# Finday Intelligence: Setup, Run and Build Guide

One place for starting the backend, running the mobile app, building the Android APK and
publishing it for clients. Windows commands are shown first (PowerShell); the mobile
commands work the same on any OS.

Project layout:

```text
backend/   Django web app + JSON API for the mobile app (PostgreSQL)
mobile/    Flutter app (Android / iOS)
docs/      Product documentation
```

For deeper detail see [README.md](README.md) (backend and multi-tenancy),
[mobile/README.md](mobile/README.md) (mobile app and API) and
[mobile/docs/push-notifications.md](mobile/docs/push-notifications.md) (push setup).

---

## 1. Prerequisites

| Tool | Used for | Check |
| --- | --- | --- |
| Python 3.12 | backend | `python --version` |
| PostgreSQL 14+ | backend database | `psql --version` |
| Flutter SDK (stable) | mobile app | `flutter --version` |
| Android Studio + Android SDK | Android build and emulator | `flutter doctor` |
| Xcode (Mac only) | iOS build | `flutter doctor` |

Run `flutter doctor` once and fix anything it marks with a red cross before building.

---

## 2. Start the backend

### 2.1 First-time setup

```powershell
cd D:\projects\Business_analytics_intelligence\backend
python -m venv venv
.\venv\Scripts\Activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `backend\.env`:

- Set `DEV_DB_USER`, `DEV_DB_PASSWORD`, `DEV_DB_HOST`, `DEV_DB_PORT` to your local PostgreSQL.
- Keep `ENVIRONMENT=development` for local work.
- Set `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` if you need the platform superadmin login.
- Leave the email settings blank locally. Emails then print to the console.

Create the database, then migrate:

```powershell
psql -U postgres -c "CREATE DATABASE bai_dev;"
python manage.py migrate
python manage.py bootstrap_superadmin
```

### 2.2 Run it

```powershell
cd D:\projects\Business_analytics_intelligence\backend
.\venv\Scripts\Activate
python manage.py runserver
```

Open:

- Web app: http://127.0.0.1:8000/
- Sign in: http://127.0.0.1:8000/accounts/login/
- Django admin: http://127.0.0.1:8000/admin/

Run it on all network interfaces when a real phone needs to reach it:

```powershell
python manage.py runserver 0.0.0.0:8000
```

Then add your PC's LAN IP to `DJANGO_ALLOWED_HOSTS` in `backend\.env`, for example
`localhost,127.0.0.1,10.0.2.2,192.168.1.20`.

### 2.3 Checks

```powershell
python manage.py check
python manage.py test
```

### 2.4 Production notes

- Set `ENVIRONMENT=production` and fill in the `PROD_*` values in `.env`.
- Generate a real `PROD_DJANGO_SECRET_KEY`:
  ```powershell
  python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
  ```
- Run `python manage.py collectstatic` before deploying.
- Run `python manage.py check --deploy` and fix what it reports.
- Deployment: `cloudbuild.yaml` builds the Docker image from `backend/` and deploys it to
  Cloud Run. The Docker image runs gunicorn on `$PORT`.

---

## 3. Run the mobile app

### 3.1 First-time setup

```powershell
cd D:\projects\Business_analytics_intelligence\mobile
flutter pub get
```

### 3.2 Run on an emulator or phone

Make sure the backend is running first (section 2.2).

| Where you run the app | Command |
| --- | --- |
| Android emulator | `flutter run` (default URL `http://10.0.2.2:8000/api/mobile/`) |
| iOS simulator or Windows/desktop | `flutter run --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/mobile/` |
| Real phone on the same Wi-Fi | `flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000/api/mobile/` |
| Deployed site | `flutter run --dart-define=API_BASE_URL=https://your-domain/api/mobile/` |

Replace `192.168.1.20` with your PC's LAN IP. Debug builds accept plain `http`; release
builds require `https`.

Sign in with the same email and password as the web app.

### 3.3 Checks

```powershell
cd D:\projects\Business_analytics_intelligence\mobile
flutter analyze
flutter test
```

---

## 4. Build the Android APK

Build the arm64 APK for phones. It is about 19 MB. A build without the target flag includes
three CPU types and is about 53 MB.

```powershell
cd D:\projects\Business_analytics_intelligence\mobile
flutter build apk --release --target-platform android-arm64
```

Output:

```text
mobile\build\app\outputs\flutter-apk\app-release.apk
```

To point a release build at the live server:

```powershell
flutter build apk --release --target-platform android-arm64 --dart-define=API_BASE_URL=https://your-domain/api/mobile/
```

### 4.1 Push notifications (optional)

Push is off unless the Firebase values are given. The app builds without them. To enable it,
add these to the build command:

```powershell
--dart-define=FIREBASE_API_KEY=... --dart-define=FIREBASE_APP_ID=... --dart-define=FIREBASE_MESSAGING_SENDER_ID=... --dart-define=FIREBASE_PROJECT_ID=...
```

Full setup, including the server's service-account key, is in
[mobile/docs/push-notifications.md](mobile/docs/push-notifications.md).

### 4.2 Build troubleshooting

| Error | Fix |
| --- | --- |
| `Type 'RemoteMessage' not found` or `Error when reading ...firebase_core...` | Stale package state: `flutter clean`, then `flutter pub get`, then build again. |
| `Could not close incremental caches` (Kotlin) | The pub cache and the project are on different drives. `mobile\android\gradle.properties` already contains `kotlin.incremental=false`. |
| `Plugin directory does not exist` (Gradle) | Stop the Gradle daemons and build again: `cd mobile\android` then `.\gradlew --stop`. |

When Gradle still fails after these steps, run `flutter clean` and build again.

---

## 5. Publish the APK for clients to download

Clients download the APK from the **Mobile app download** link in their web app
(`/accounts/mobile-app/download/`). That link serves whichever version is marked
**current** in the superadmin console.

1. Sign in to the web app as the platform superadmin.
2. Open the superadmin console's **Mobile app** page at `/superadmin/<env>/mobile-app/`, where `<env>` is the environment in use (for example `dev` or `prod`).
3. Upload `app-release.apk` and add a version number (for example `1.0.1`) and notes.
4. Mark the new version as **current**.

Uploaded APKs are stored in the database, so they survive server restarts. Alternatively,
host the APK elsewhere (Google Drive or GitHub releases) and set its direct-download link
as the version's `download_url`. Clients are then redirected to that link.

Version numbers: raise the version in `mobile/pubspec.yaml` (`version: 1.0.1+2`) before each
release build, so clients can tell builds apart.

---

## 6. Quick reference

```powershell
# Backend
cd D:\projects\Business_analytics_intelligence\backend
.\venv\Scripts\Activate
python manage.py runserver

# Mobile app (emulator)
cd D:\projects\Business_analytics_intelligence\mobile
flutter run

# Release APK (arm64, about 19 MB)
flutter build apk --release --target-platform android-arm64
```
