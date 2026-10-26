import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../models/entry_options.dart';
import '../models/entry_record.dart';

final entryOptionsProvider = FutureProvider.autoDispose<EntryOptions>((ref) {
  return ref.watch(entriesRepositoryProvider).options();
});

/// Entries recorded on one day, keyed by the ISO date (`2026-10-24`).
final dayEntriesProvider = FutureProvider.autoDispose
    .family<List<EntryRecord>, String>((ref, date) {
      return ref.watch(entriesRepositoryProvider).forDate(date);
    });
