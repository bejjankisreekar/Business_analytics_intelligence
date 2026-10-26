import 'package:dio/dio.dart';
import 'package:finday_mobile/core/network/api_exception.dart';
import 'package:flutter_test/flutter_test.dart';

DioException _error(int status, Object data) {
  final request = RequestOptions(path: 'entries/');
  return DioException(
    requestOptions: request,
    response: Response(requestOptions: request, statusCode: status, data: data),
  );
}

void main() {
  test('reads the backend error message and field errors', () {
    final e = ApiException.fromDio(
      _error(400, {
        'error': 'Check the highlighted fields.',
        'code': 'validation',
        'fields': {
          'category': ['Required.'],
        },
      }),
    );
    expect(e.message, 'Check the highlighted fields.');
    expect(e.fieldErrors['category'], 'Required.');
    expect(e.isNetworkError, isFalse);
  });

  test('a response-less failure is a network error', () {
    final e = ApiException.fromDio(
      DioException(requestOptions: RequestOptions(path: 'x')),
    );
    expect(e.isNetworkError, isTrue);
  });

  test('401 is unauthorized', () {
    expect(
      ApiException.fromDio(_error(401, {'error': 'Sign in again.'}))
          .isUnauthorized,
      isTrue,
    );
  });
}
