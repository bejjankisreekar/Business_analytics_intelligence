class AppConstants {
  const AppConstants._();

  static const Duration requestTimeout = Duration(seconds: 15);

  /// Shortest time the splash stays up, so the animation plays even with no saved session.
  static const Duration splashMinimum = Duration(milliseconds: 1800);
}
