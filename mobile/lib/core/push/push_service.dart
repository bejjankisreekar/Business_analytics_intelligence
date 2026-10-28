import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/config/env.dart';
import '../../app/messenger.dart';
import '../../app/router.dart';
import '../../features/requests/application/requests_providers.dart';

/// Push notifications for new customer requests.
///
/// Firebase is set up from build-time values (see [Env]) instead of a google-services.json, so an
/// app built without them simply has no push and everything else works as before.
class PushService {
  PushService(this._ref);

  final Ref _ref;

  bool _firebaseTried = false;
  bool _firebaseReady = false;
  String? _registeredToken;
  StreamSubscription<String>? _refreshSub;
  StreamSubscription<RemoteMessage>? _foregroundSub;
  StreamSubscription<RemoteMessage>? _openedSub;

  static bool get configured =>
      Env.firebaseApiKey.isNotEmpty &&
      Env.firebaseAppId.isNotEmpty &&
      Env.firebaseSenderId.isNotEmpty &&
      Env.firebaseProjectId.isNotEmpty;

  Future<bool> _ensureFirebase() async {
    if (_firebaseTried) return _firebaseReady;
    _firebaseTried = true;
    if (!configured) return false;
    try {
      await Firebase.initializeApp(
        options: const FirebaseOptions(
          apiKey: Env.firebaseApiKey,
          appId: Env.firebaseAppId,
          messagingSenderId: Env.firebaseSenderId,
          projectId: Env.firebaseProjectId,
        ),
      );
      _firebaseReady = true;
    } catch (error) {
      debugPrint('Push is off, Firebase could not start: $error');
    }
    return _firebaseReady;
  }

  /// Starts once the person is signed in and may use customer requests. Safe to call again.
  Future<void> start() async {
    if (!await _ensureFirebase()) return;
    try {
      final messaging = FirebaseMessaging.instance;
      final permission = await messaging.requestPermission();
      if (permission.authorizationStatus == AuthorizationStatus.denied) return;

      // getToken() and onTokenRefresh are marked deprecated in firebase_messaging 17, whose
      // replacement (register / onRegistered) hands out a Firebase installation ID. The server
      // sends with the classic FCM registration token (HTTP v1 `token`), which these still return,
      // so they stay until the new ID is confirmed to work as a send target.
      // ignore: deprecated_member_use
      final token = await messaging.getToken();
      if (token != null) await _register(token);

      // ignore: deprecated_member_use
      _refreshSub ??= messaging.onTokenRefresh.listen(_register);
      _foregroundSub ??= FirebaseMessaging.onMessage.listen(_onForeground);
      _openedSub ??= FirebaseMessaging.onMessageOpenedApp.listen(_open);

      // The app was closed and the owner tapped the notification to open it.
      final launch = await messaging.getInitialMessage();
      if (launch != null) _open(launch);
    } catch (error) {
      debugPrint('Push could not start: $error');
    }
  }

  Future<void> _register(String token) async {
    try {
      await _ref.read(requestsRepositoryProvider).registerDevice(token);
      _registeredToken = token;
    } catch (error) {
      debugPrint('Could not register this phone for push: $error');
    }
  }

  /// Forgets this phone on the server so a signed-out phone stops getting the owner's alerts.
  /// Call it BEFORE the saved sign-in is cleared; it never throws.
  Future<void> stop() async {
    final token = _registeredToken;
    _registeredToken = null;
    if (token == null) return;
    try {
      await _ref
          .read(requestsRepositoryProvider)
          .unregisterDevice(token)
          .timeout(const Duration(seconds: 4));
    } catch (error) {
      debugPrint('Could not unregister this phone: $error');
    }
  }

  /// The app is open: no system notification is shown, so show our own banner and refresh the lists.
  void _onForeground(RemoteMessage message) {
    _ref.invalidate(waitingCountProvider);
    _ref.invalidate(requestsListProvider);
    final title = message.notification?.title ?? 'Customer request';
    final body = message.notification?.body ?? '';
    rootMessengerKey.currentState
      ?..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(body.isEmpty ? title : '$title - $body'),
          duration: const Duration(seconds: 10),
          action: SnackBarAction(label: 'Open', onPressed: () => _open(message)),
        ),
      );
  }

  void _open(RemoteMessage message) {
    _ref.invalidate(waitingCountProvider);
    _ref.invalidate(requestsListProvider);
    final number = int.tryParse('${message.data['number']}');
    if (number == null) return;
    _ref.read(routerProvider).push('${AppRoutes.request}/$number');
  }
}

final pushServiceProvider = Provider<PushService>((ref) {
  final service = PushService(ref);
  ref.onDispose(() {
    // Streams are cancelled by the platform when the app process ends.
  });
  return service;
});
