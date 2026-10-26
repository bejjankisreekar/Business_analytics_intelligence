import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../data/reports_repository.dart';

final dashboardProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).dashboard();
});

/// Daily performance for an inclusive range, keyed by `(from, to)` ISO dates.
final dailyPerformanceProvider = FutureProvider.autoDispose
    .family<Json, (String, String)>((ref, range) {
      return ref
          .watch(reportsRepositoryProvider)
          .dailyPerformance(from: range.$1, to: range.$2);
    });

final intelligenceProvider = FutureProvider.autoDispose.family<Json, String>((
  ref,
  period,
) {
  return ref.watch(reportsRepositoryProvider).intelligence(period);
});

/// Monthly summary, keyed by `(kind, granularity, period)`.
final monthlySummaryProvider = FutureProvider.autoDispose
    .family<Json, (String, String, String)>((ref, key) {
      return ref
          .watch(reportsRepositoryProvider)
          .monthlySummary(kind: key.$1, granularity: key.$2, period: key.$3);
    });

final cashPositionProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).cashPosition();
});

/// Financial reports, keyed by `(period, flow)`; flow is `all`, `cash` or `bank` (cash flow only).
final financialReportsProvider = FutureProvider.autoDispose
    .family<Json, (String, String)>((ref, key) {
      return ref
          .watch(reportsRepositoryProvider)
          .financialReports(key.$1, flow: key.$2);
    });

final bankAccountsProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).bankAccounts();
});

final billingProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).billing();
});

final vendorsProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).vendors();
});

final agingProvider = FutureProvider.autoDispose<Json>((ref) {
  return ref.watch(reportsRepositoryProvider).aging();
});

final dayDetailProvider = FutureProvider.autoDispose.family<Json, String>((
  ref,
  date,
) {
  return ref.watch(reportsRepositoryProvider).dayDetail(date);
});

/// Keyed by `(kind, name, from, to)`.
final categoryDetailProvider = FutureProvider.autoDispose
    .family<Json, (String, String, String, String)>((ref, key) {
      return ref
          .watch(reportsRepositoryProvider)
          .categoryDetail(kind: key.$1, name: key.$2, from: key.$3, to: key.$4);
    });

/// Category statement, keyed by `(kind, category name, sub-category id or '', from, to)`.
final categoryStatementProvider = FutureProvider.autoDispose
    .family<Json, (String, String, String, String, String)>((ref, key) {
      return ref
          .watch(reportsRepositoryProvider)
          .categoryDetail(
            kind: key.$1,
            name: key.$2,
            subcategory: key.$3,
            from: key.$4,
            to: key.$5,
          );
    });

/// Keyed by `(kind, id)`; kind is `bank` or `vendor`.
final ledgerProvider = FutureProvider.autoDispose
    .family<Json, (String, String)>((ref, key) {
      return ref
          .watch(reportsRepositoryProvider)
          .ledger(kind: key.$1, id: key.$2);
    });
