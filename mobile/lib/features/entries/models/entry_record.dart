import 'entry_type.dart';

/// One saved entry, as listed by `entries/`.
class EntryRecord {
  const EntryRecord({
    required this.id,
    required this.type,
    required this.category,
    required this.subcategory,
    required this.amount,
    required this.paymentMode,
    required this.note,
  });

  factory EntryRecord.fromJson(Map<String, dynamic> json) {
    return EntryRecord(
      id: json['id'].toString(),
      type: EntryType.fromApi(json['type'] as String),
      category: json['category'] as String?,
      subcategory: json['subcategory'] as String?,
      amount: num.tryParse(json['amount'].toString()) ?? 0,
      paymentMode: json['payment_mode'] as String? ?? '',
      note: json['note'] as String? ?? '',
    );
  }

  final String id;
  final EntryType type;
  final String? category;
  final String? subcategory;
  final num amount;
  final String paymentMode;
  final String note;

  String get title {
    final base = category ?? 'Uncategorised';
    return subcategory == null ? base : '$base · $subcategory';
  }
}
