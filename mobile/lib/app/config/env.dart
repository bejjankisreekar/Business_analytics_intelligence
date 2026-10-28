/// Build-time configuration. Override per build with --dart-define.
///
/// Example: flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000/api/mobile/
class Env {
  const Env._();

  /// Default points at the Django dev server from an Android emulator.
  /// iOS simulators and desktop use http://127.0.0.1:8000/api/mobile/ instead.
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000/api/mobile/',
  );

  static const String appName = 'Finday Intelligence';

  /// Push notifications. Leave these unset and the app builds and runs without push. They come from
  /// the Firebase console (Project settings > Your apps > Android); see docs/push-notifications.md.
  ///
  ///   flutter build apk --dart-define=FIREBASE_API_KEY=... --dart-define=FIREBASE_APP_ID=... \
  ///     --dart-define=FIREBASE_MESSAGING_SENDER_ID=... --dart-define=FIREBASE_PROJECT_ID=...
  static const String firebaseApiKey = String.fromEnvironment('FIREBASE_API_KEY');
  static const String firebaseAppId = String.fromEnvironment('FIREBASE_APP_ID');
  static const String firebaseSenderId = String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID');
  static const String firebaseProjectId = String.fromEnvironment('FIREBASE_PROJECT_ID');
}
