import 'package:dio/dio.dart';

import '../../app/config/env.dart';
import '../constants/app_constants.dart';
import '../storage/token_storage.dart';
import 'api_endpoints.dart';
import 'api_exception.dart';

/// Dio wrapper that attaches the bearer token and reports an expired session.
class ApiClient {
  ApiClient({required this._tokenStorage, required this._onSessionExpired})
    : _dio = Dio(
        BaseOptions(
          baseUrl: Env.apiBaseUrl,
          connectTimeout: AppConstants.requestTimeout,
          receiveTimeout: AppConstants.requestTimeout,
          headers: {'Accept': 'application/json'},
        ),
      ) {
    _dio.interceptors.add(
      InterceptorsWrapper(onRequest: _onRequest, onError: _onError),
    );
  }

  final TokenStorage _tokenStorage;
  final void Function() _onSessionExpired;
  final Dio _dio;

  /// Runs a request and converts Dio errors into [ApiException].
  Future<Response<T>> send<T>(
    Future<Response<T>> Function(Dio dio) request,
  ) async {
    try {
      return await request(_dio);
    } on DioException catch (error) {
      throw ApiException.fromDio(error);
    }
  }

  Future<void> _onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    final token = await _tokenStorage.read();
    if (token != null) options.headers['Authorization'] = 'Bearer $token';
    handler.next(options);
  }

  void _onError(DioException error, ErrorInterceptorHandler handler) {
    // A 401 on login just means a wrong password; anywhere else the token is no longer good.
    final isLogin = error.requestOptions.path == ApiEndpoints.login;
    if (error.response?.statusCode == 401 && !isLogin) _onSessionExpired();
    handler.next(error);
  }
}
