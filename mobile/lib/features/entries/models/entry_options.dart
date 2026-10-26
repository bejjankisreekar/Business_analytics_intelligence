import 'entry_type.dart';

class NamedItem {
  const NamedItem({
    required this.id,
    required this.name,
    this.children = const [],
  });

  factory NamedItem.fromJson(Map<String, dynamic> json) => NamedItem(
    id: json['id'].toString(),
    name: json['name'] as String,
    children: ((json['children'] as List?) ?? const [])
        .map((c) => NamedItem.fromJson(c as Map<String, dynamic>))
        .toList(),
  );

  final String id;
  final String name;

  /// Items under a sub-category (e.g. a doctor under Cardiology); empty for everything else.
  final List<NamedItem> children;
}

class EntryCategory extends NamedItem {
  const EntryCategory({
    required super.id,
    required super.name,
    required this.subcategories,
  });

  factory EntryCategory.fromJson(Map<String, dynamic> json) {
    return EntryCategory(
      id: json['id'].toString(),
      name: json['name'] as String,
      subcategories: ((json['subcategories'] as List?) ?? const [])
          .map((s) => NamedItem.fromJson(s as Map<String, dynamic>))
          .toList(),
    );
  }

  final List<NamedItem> subcategories;
}

class PaymentModeOption {
  const PaymentModeOption({required this.value, required this.label});

  final String value;
  final String label;

  static const bank = 'BANK';
}

/// Everything the entry form needs, from `entry-options/`.
class EntryOptions {
  const EntryOptions({
    required this.categories,
    required this.paymentModes,
    required this.bankAccounts,
    required this.vendors,
  });

  factory EntryOptions.fromJson(Map<String, dynamic> json) {
    final categories = json['categories'] as Map<String, dynamic>;
    return EntryOptions(
      categories: {
        for (final type in EntryType.values)
          type: ((categories[type.apiValue] as List?) ?? const [])
              .map((c) => EntryCategory.fromJson(c as Map<String, dynamic>))
              .toList(),
      },
      paymentModes: (json['payment_modes'] as List)
          .map(
            (m) => PaymentModeOption(
              value: (m as Map<String, dynamic>)['value'] as String,
              label: m['label'] as String,
            ),
          )
          .toList(),
      bankAccounts: ((json['bank_accounts'] as List?) ?? const [])
          .map((b) => NamedItem.fromJson(b as Map<String, dynamic>))
          .toList(),
      vendors: ((json['vendors'] as List?) ?? const [])
          .map((v) => v.toString())
          .toList(),
    );
  }

  final Map<EntryType, List<EntryCategory>> categories;
  final List<PaymentModeOption> paymentModes;
  final List<NamedItem> bankAccounts;
  final List<String> vendors;
}
