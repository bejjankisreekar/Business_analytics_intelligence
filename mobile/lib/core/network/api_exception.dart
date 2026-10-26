import 'package:dio/dio.dart';

/// A failed API call, with the backend's message converted for display.
///
/// The backend returns `{"error": "...", "code": "...", "fields": {"name": ["..."]}}`.
class ApiException implements Exception {
  const ApiException({
    required this.message,
    this.code,
    this.statusCode,
    this.fieldErrors = const {},
  });

  final String message;
  final String? code;

  /// Null when the request never got a response (no connection, timeout).
  final int? statusCode;

  /// First error message per field, for showing next to form inputs.
  final Map<String, String> fieldErrors;

  bool get isUnauthorized => statusCode == 401;

  bool get isNetworkError => statusCode == null;

  factory ApiException.fromDio(DioException error) {
    final response = error.response;
    if (response == null) {
      return const ApiException(
        message:
            'Cannot reach the server. Check your connection and try again.',
      );
    }

    final data = response.data;
    if (data is Map) {
      final fields = <String, String>{};
      final rawFields = data['fields'];
      if (rawFields is Map) {
        rawFields.forEach((key, value) {
          if (value is List && value.isNotEmpty)
            fields[key.toString()] = value.first.toString();
        });
      }
      final message = data['error'];
      if (message is String) {
        return ApiException(
          message: message,
          code: data['code']?.toString(),
          statusCode: response.statusCode,
          fieldErrors: fields,
        );
      }
    }

    return ApiException(
      message: 'Something went wrong. Please try again.',
      statusCode: response.statusCode,
    );
  }

  @override
  String toString() => 'ApiException($statusCode, $code): $message';
}

/// Turns any error from a repository call into a message that is safe to show.
String userMessage(Object error) {
  if (error is ApiException) return error.message;
  return 'Something went wrong. Please try again.';
}
