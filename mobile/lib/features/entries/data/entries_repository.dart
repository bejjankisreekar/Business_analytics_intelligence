import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';
import '../models/entry_options.dart';
import '../models/entry_record.dart';

class EntriesRepository {
  EntriesRepository(this._api);

  final ApiClient _api;

  Future<EntryOptions> options() async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(ApiEndpoints.entryOptions),
    );
    return EntryOptions.fromJson(response.data!);
  }

  /// All entries (revenue, expense, purchase) recorded on [isoDate], newest first.
  Future<List<EntryRecord>> forDate(String isoDate) async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(
        ApiEndpoints.entries,
        queryParameters: {'date': isoDate},
      ),
    );
    return (response.data!['entries'] as List)
        .map((e) => EntryRecord.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Creates one entry. Throws an ApiException whose `fieldErrors` name the invalid inputs.
  Future<void> create(Map<String, dynamic> payload) async {
    await _api.send(
      (dio) =>
          dio.post<Map<String, dynamic>>(ApiEndpoints.entries, data: payload),
    );
  }
}
