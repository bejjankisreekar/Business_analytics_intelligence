import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_client.dart';
import '../core/storage/token_storage.dart';
import '../features/auth/application/session_controller.dart';
import '../features/auth/data/auth_repository.dart';
import '../features/reports/data/reports_repository.dart';
import '../features/entries/data/entries_repository.dart';

/// Composition root: providers shared across features.

final tokenStorageProvider = Provider<TokenStorage>((ref) => TokenStorage());

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(
    tokenStorage: ref.watch(tokenStorageProvider),
    // Read lazily, so building the client never touches the session controller.
    onSessionExpired: () =>
        ref.read(sessionControllerProvider.notifier).expire(),
  );
});

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  return AuthRepository(ref.watch(apiClientProvider));
});

final entriesRepositoryProvider = Provider<EntriesRepository>((ref) {
  return EntriesRepository(ref.watch(apiClientProvider));
});

final reportsRepositoryProvider = Provider<ReportsRepository>((ref) {
  return ReportsRepository(ref.watch(apiClientProvider));
});
