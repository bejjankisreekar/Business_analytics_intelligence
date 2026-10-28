/// The signed-in user, as returned by `login/` and `me/`.
class AppUser {
  const AppUser({
    required this.email,
    required this.name,
    required this.role,
    required this.organizationName,
    this.customerRequests = false,
  });

  factory AppUser.fromJson(Map<String, dynamic> json) {
    final user = json['user'] as Map<String, dynamic>;
    final organization =
        (json['organization'] as Map<String, dynamic>?) ?? const {};
    final features = (json['features'] as Map<String, dynamic>?) ?? const {};
    return AppUser(
      email: user['email'] as String? ?? '',
      name: (user['name'] as String?)?.trim() ?? '',
      role: user['role'] as String? ?? '',
      organizationName: organization['name'] as String? ?? '',
      customerRequests: features['customer_requests'] == true,
    );
  }

  final String email;
  final String name;
  final String role;
  final String organizationName;

  /// Whether the Customer requests add-on is on for this account (shops and restaurants).
  final bool customerRequests;

  /// Name for greetings; falls back to the email when no name is set.
  String get displayName => name.isEmpty ? email : name;
}
