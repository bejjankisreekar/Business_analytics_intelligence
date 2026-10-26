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
}
