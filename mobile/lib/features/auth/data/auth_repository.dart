import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';
import '../models/app_user.dart';

class AuthResult {
  const AuthResult({required this.user, required this.token});

  factory AuthResult.fromJson(Map<String, dynamic> json) {
    return AuthResult(
      user: AppUser.fromJson(json),
      token: json['token'] as String,
    );
  }

  final AppUser user;
  final String token;
}

class AuthRepository {
  AuthRepository(this._api);

  final ApiClient _api;

  Future<AuthResult> login({
    required String email,
    required String password,
  }) async {
    final response = await _api.send(
      (dio) => dio.post<Map<String, dynamic>>(
        ApiEndpoints.login,
        data: {'email': email, 'password': password},
      ),
    );
    return AuthResult.fromJson(response.data!);
  }

  Future<AppUser> me() async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(ApiEndpoints.me),
    );
    return AppUser.fromJson(response.data!);
  }
}
