import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';

typedef Json = Map<String, dynamic>;

/// Read-only report endpoints. Amounts arrive as strings; the screens parse them with `parseNum`.
class ReportsRepository {
  ReportsRepository(this._api);

  final ApiClient _api;

  Future<Json> _get(String path, [Json? query]) async {
    final response = await _api.send(
      (dio) => dio.get<Json>(path, queryParameters: query),
    );
    return response.data!;
  }

  /// Query for a period key. A custom range travels as `custom:YYYY-MM-DD:YYYY-MM-DD`.
  static Json periodQuery(String key) {
    if (key.startsWith('custom:')) {
      final parts = key.split(':');
      if (parts.length == 3) {
        return {'period': 'custom', 'from': parts[1], 'to': parts[2]};
      }
    }
    return {'period': key};
  }

  Future<Json> billing() => _get(ApiEndpoints.billing);

  Future<Json> dashboard() => _get(ApiEndpoints.dashboard);

  Future<Json> dailyPerformance({required String from, required String to}) =>
      _get(ApiEndpoints.dailyPerformance, {'from': from, 'to': to});

  Future<Json> intelligence(String period) =>
      _get(ApiEndpoints.intelligence, periodQuery(period));

  Future<Json> monthlySummary({
    String kind = 'sale',
    String granularity = 'monthly',
    String period = 'this_fy',
  }) => _get(ApiEndpoints.monthlySummary, {
    'kind': kind,
    'granularity': granularity,
    ...periodQuery(period),
  });

  Future<Json> cashPosition() => _get(ApiEndpoints.cashPosition);

  Future<Json> financialReports(String period, {String flow = 'all'}) => _get(
    ApiEndpoints.financialReports,
    {...periodQuery(period), 'flow': flow},
  );

  Future<Json> bankAccounts() => _get(ApiEndpoints.bankAccounts);

  Future<Json> vendors() => _get(ApiEndpoints.vendors);

  Future<Json> aging() => _get(ApiEndpoints.aging);

  Future<Json> dayDetail(String date) =>
      _get(ApiEndpoints.dayDetail, {'date': date});

  Future<Json> categoryDetail({
    required String kind,
    required String name,
    required String from,
    required String to,
    String? subcategory,
  }) => _get(ApiEndpoints.categoryDetail, {
    'kind': kind,
    'name': name,
    'from': from,
    'to': to,
    if (subcategory != null && subcategory.isNotEmpty)
      'subcategory': subcategory,
  });

  Future<Json> ledger({required String kind, required String id}) =>
      _get(ApiEndpoints.ledger, {'kind': kind, 'id': id});
}
