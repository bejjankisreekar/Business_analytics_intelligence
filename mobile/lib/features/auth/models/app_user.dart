/// The signed-in user, as returned by `login/` and `me/`.
class AppUser {
  const AppUser({
    required this.email,
    required this.name,
    required this.role,
    required this.organizationName,
  });

  factory AppUser.fromJson(Map<String, dynamic> json) {
    final user = json['user'] as Map<String, dynamic>;
    final organization =
        (json['organization'] as Map<String, dynamic>?) ?? const {};
    return AppUser(
      email: user['email'] as String? ?? '',
      name: (user['name'] as String?)?.trim() ?? '',
      role: user['role'] as String? ?? '',
      organizationName: organization['name'] as String? ?? '',
    );
  }

  final String email;
  final String name;
  final String role;
  final String organizationName;

  /// Name for greetings; falls back to the email when no name is set.
  String get displayName => name.isEmpty ? email : name;
}
