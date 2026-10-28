# Push notifications for customer requests

Shops and restaurants that have **Customer Requests** get, in the mobile app:

- a **Requests** tab in the bottom bar, with a count badge for requests still waiting for a quote;
- a **push notification** on the owner's phone when a customer sends a list, or changes a waiting list.

Accounts without Customer Requests (hospitals, other business types, or a client a superadmin limited to
other pages) never see the tab and are never sent a notification.

The tab works with no extra setup. **Push needs a one-time Firebase setup**, below. Until it is done the app
builds and runs normally and simply gets no push; the owner is still emailed and still sees the badge.

## What you need to do once

You need a Google account to own a Firebase project. Nothing here costs money.

### 1. Create the Firebase project and Android app

1. Open <https://console.firebase.google.com> and create a project (or pick an existing one).
2. Add an **Android app** with the package name `com.finday.finday_mobile`.
3. You can skip downloading `google-services.json`. This app does not use it.

### 2. Collect the four app values

| Build value (`--dart-define`) | Where to find it in the Firebase console |
|---|---|
| `FIREBASE_PROJECT_ID` | Project settings, General: **Project ID** |
| `FIREBASE_APP_ID` | Project settings, General, Your apps, Android app: **App ID** (looks like `1:123456789:android:abc123`) |
| `FIREBASE_MESSAGING_SENDER_ID` | Project settings, Cloud Messaging: **Sender ID** (the project number) |
| `FIREBASE_API_KEY` | Project settings, General: **Web API Key** |

Build the app with them:

```bash
flutter build apk --release ^
  --dart-define=API_BASE_URL=https://findayintelligence.com/api/mobile/ ^
  --dart-define=FIREBASE_PROJECT_ID=your-project-id ^
  --dart-define=FIREBASE_APP_ID=1:123456789:android:abc123 ^
  --dart-define=FIREBASE_MESSAGING_SENDER_ID=123456789 ^
  --dart-define=FIREBASE_API_KEY=AIza...
```

(On macOS or Linux use `\` instead of `^` at the end of each line.) If any of the four is missing, push is off
in that build.

### 3. Give the server permission to send

1. Firebase console, Project settings, **Service accounts**, **Generate new private key**. This downloads a JSON file.
2. Make sure the **Firebase Cloud Messaging API (V1)** is enabled for the project (Project settings, Cloud Messaging).
3. On the production server set **one** of these environment variables:
   - `FCM_SERVICE_ACCOUNT_JSON` = the entire contents of that JSON file, on one line; or
   - `FCM_SERVICE_ACCOUNT_FILE` = the path to the file on the server.
4. Restart the server.

Treat that JSON file like a password: it can send notifications as your project. Do not commit it.

## How it works

1. The owner signs in on the phone. The app asks permission for notifications and registers the phone with the server.
2. A customer sends or changes a list. The server emails the owner and sends a push to every phone signed in as
   the shop's owner, admin or manager.
3. If the app is **closed or in the background**, Android shows the notification (sound, pops up). Tapping it opens
   that request.
4. If the app is **open**, a banner appears with an **Open** button and the Requests list refreshes.
5. Signing out removes the phone from the server, so it stops receiving the shop's alerts. A phone that
   uninstalls the app is forgotten the next time Google reports it as gone.

## Checking that it works

1. Sign in on the phone as the shop owner. Allow notifications.
2. On another device open the shop's request link and send a list.
3. The phone should show **New request #N** within a few seconds.

If nothing arrives: check the four values were part of the build (a build without them has no push), that
`FCM_SERVICE_ACCOUNT_JSON` or `FCM_SERVICE_ACCOUNT_FILE` is set and valid on the server, and that the server's
log does not show "Could not log in to Firebase Cloud Messaging".

## Not covered yet

- **iPhone.** iOS push needs an Apple developer account and an APNs key uploaded to Firebase. Only Android is set up.
- **Not tried on a real phone.** The server side and the app's screens are covered by automated tests, but the
  Android build and a live notification could not be tried where this was written (no Android toolchain and no
  Firebase project). Do the check above once after the first build.
- **Token API.** The app asks Firebase for the phone's token with `getToken()`, which `firebase_messaging` 17 marks
  as deprecated in favour of `register()`. It still works and returns the registration token the server sends to.
  If a later version removes it, the server must be switched to the new identifier together with the app.
