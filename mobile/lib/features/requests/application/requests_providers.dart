import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../auth/application/session_controller.dart';
import '../../reports/data/reports_repository.dart' show Json;
import '../data/requests_repository.dart';

final requestsRepositoryProvider = Provider<RequestsRepository>((ref) {
  return RequestsRepository(ref.watch(apiClientProvider));
});

/// What the Requests tab is showing: a day, one status or payment filter, and a search.
class RequestsFilter {
  const RequestsFilter({
    this.day = '',
    this.status = '',
    this.payment = '',
    this.query = '',
  });

  /// Empty means the server's default, which is today.
  final String day;
  final String status;
  final String payment;
  final String query;

  RequestsFilter copyWith({
    String? day,
    String? status,
    String? payment,
    String? query,
  }) => RequestsFilter(
    day: day ?? this.day,
    status: status ?? this.status,
    payment: payment ?? this.payment,
    query: query ?? this.query,
  );
}

class RequestsFilterNotifier extends Notifier<RequestsFilter> {
  @override
  RequestsFilter build() => const RequestsFilter();

  void setDay(String day) => state = state.copyWith(day: day);

  /// Status and payment are one choice (All, Waiting, Quoted, Unpaid, Paid), so setting one clears the other.
  void setStatus({String status = '', String payment = ''}) =>
      state = state.copyWith(status: status, payment: payment);

  void setQuery(String query) => state = state.copyWith(query: query.trim());
}

final requestsFilterProvider =
    NotifierProvider<RequestsFilterNotifier, RequestsFilter>(
      RequestsFilterNotifier.new,
    );

final requestsListProvider = FutureProvider.autoDispose<Json>((ref) {
  final filter = ref.watch(requestsFilterProvider);
  return ref
      .watch(requestsRepositoryProvider)
      .list(
        day: filter.day,
        query: filter.query,
        status: filter.status,
        payment: filter.payment,
      );
});

final requestDetailProvider = FutureProvider.autoDispose.family<Json, int>((
  ref,
  number,
) {
  return ref.watch(requestsRepositoryProvider).detail(number);
});

/// Requests still waiting for a quote, across all days: the number on the tab's badge.
final waitingCountProvider = FutureProvider<int>((ref) async {
  final user = ref.watch(sessionControllerProvider).user;
  if (user == null || !user.customerRequests) return 0;
  final data = await ref.watch(requestsRepositoryProvider).list();
  return (data['waiting_total'] as num?)?.toInt() ?? 0;
});
