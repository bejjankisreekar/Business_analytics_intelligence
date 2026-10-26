import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/storage/token_storage.dart';
import '../data/auth_repository.dart';
import '../models/app_user.dart';

enum SessionStatus { unknown, signedOut, signedIn }

class SessionState {
  const SessionState._(this.status, this.user);

  const SessionState.unknown() : this._(SessionStatus.unknown, null);

  const SessionState.signedOut() : this._(SessionStatus.signedOut, null);

  const SessionState.signedIn(AppUser user)
    : this._(SessionStatus.signedIn, user);

  final SessionStatus status;
  final AppUser? user;
}

/// Owns sign-in state. The router listens to this to decide which screen to show.
class SessionController extends Notifier<SessionState> {
  @override
  SessionState build() => const SessionState.unknown();

  AuthRepository get _auth => ref.read(authRepositoryProvider);

  TokenStorage get _tokens => ref.read(tokenStorageProvider);

  /// Called from the splash screen. Restores a saved session if its token still works.
  ///
  /// Rethrows network errors and leaves the state unknown, so the splash screen can offer a
  /// retry instead of signing out someone whose session is fine but who is offline.
  ///
  /// [minimumWait] holds the result back until it completes, so the splash animation can finish.
  Future<void> restore({Future<void>? minimumWait}) async {
    final token = await _tokens.read();
    if (token == null) {
      await minimumWait;
      state = const SessionState.signedOut();
      return;
    }

    try {
      final user = await _auth.me();
      await minimumWait;
      state = SessionState.signedIn(user);
    } on ApiException catch (error) {
      if (error.isNetworkError) rethrow;
      await _tokens.clear();
      await minimumWait;
      state = const SessionState.signedOut();
    }
  }

  Future<void> signIn({required String email, required String password}) async {
    final result = await _auth.login(email: email.trim(), password: password);
    await _tokens.save(result.token);
    state = SessionState.signedIn(result.user);
  }

  Future<void> signOut() async {
    await _tokens.clear();
    state = const SessionState.signedOut();
  }

  /// Called by the API client when the server rejects the saved token.
  Future<void> expire() => signOut();
}

final sessionControllerProvider =
    NotifierProvider<SessionController, SessionState>(SessionController.new);
