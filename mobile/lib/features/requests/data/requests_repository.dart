import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';
import '../../reports/data/reports_repository.dart' show Json;

/// Customer Requests: the list, one request, quoting, payment, and registering this phone for
/// push notifications. Amounts arrive as strings; the screens parse them with `parseNum`.
class RequestsRepository {
  RequestsRepository(this._api);

  final ApiClient _api;

  /// [day] is empty (the server shows today), `today`, `yesterday`, `all` or an ISO date.
  Future<Json> list({
    String day = '',
    String query = '',
    String status = '',
    String payment = '',
  }) async {
    final response = await _api.send(
      (dio) => dio.get<Json>(
        ApiEndpoints.customerRequests,
        queryParameters: {
          if (day.isNotEmpty) 'day': day,
          if (query.isNotEmpty) 'q': query,
          if (status.isNotEmpty) 'status': status,
          if (payment.isNotEmpty) 'payment': payment,
        },
      ),
    );
    return response.data!;
  }

  Future<Json> detail(int number) async {
    final response = await _api.send(
      (dio) => dio.get<Json>('${ApiEndpoints.customerRequests}$number/'),
    );
    return response.data!;
  }

  /// Saves availability, rates and remarks for every item; [send] also sends the quote.
  /// [version] is the list the owner was looking at, so a list the customer changed meanwhile is
  /// refused instead of quoted.
  Future<Json> saveQuote(
    int number, {
    required List<Json> items,
    required bool send,
    required String version,
  }) async {
    final response = await _api.send(
      (dio) => dio.post<Json>(
        '${ApiEndpoints.customerRequests}$number/quote/',
        data: {'items': items, 'send': send, 'version': version},
      ),
    );
    return response.data!;
  }

  /// [mode] is `CASH` or `BANK` (shown as Online); it only matters when [paid].
  Future<Json> setPayment(
    int number, {
    required bool paid,
    String mode = 'CASH',
  }) async {
    final response = await _api.send(
      (dio) => dio.post<Json>(
        '${ApiEndpoints.customerRequests}$number/payment/',
        data: {'payment': paid ? 'paid' : 'pending', 'mode': mode},
      ),
    );
    return response.data!;
  }

  Future<void> registerDevice(String token, {String platform = 'android'}) =>
      _api.send(
        (dio) => dio.post<Json>(
          ApiEndpoints.devices,
          data: {'token': token, 'platform': platform},
        ),
      );

  Future<void> unregisterDevice(String token) => _api.send(
    (dio) => dio.delete<Json>(ApiEndpoints.devices, data: {'token': token}),
  );
}
