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

/// How an organization's categories are laid out: the group levels above the item, the item's
/// own level name, the common levels, and the names already used (to suggest while typing).
class CategoryStructure {
  const CategoryStructure({
    required this.groups,
    required this.item,
    required this.commons,
    required this.pathOptions,
    required this.commonOptions,
  });

  /// Category > Sub-category > Item, with no common levels.
  static const standard = CategoryStructure(
    groups: ['Category', 'Sub-category'],
    item: 'Item',
    commons: [],
    pathOptions: [[], []],
    commonOptions: {},
  );

  factory CategoryStructure.fromJson(Map<String, dynamic> json) => CategoryStructure(
    groups: (json['groups'] as List).map((g) => g.toString()).toList(),
    item: json['item'].toString(),
    commons: (json['commons'] as List).map((c) => c.toString()).toList(),
    pathOptions: ((json['pathOptions'] as List?) ?? const [])
        .map((l) => (l as List).map((v) => v.toString()).toList())
        .toList(),
    commonOptions: {
      for (final e in ((json['commonOptions'] as Map?) ?? const {}).entries)
        e.key.toString(): (e.value as List).map((v) => v.toString()).toList(),
    },
  );

  final List<String> groups;
  final String item;
  final List<String> commons;
  final List<List<String>> pathOptions;
  final Map<String, List<String>> commonOptions;
}

/// A sub-category, with its path such as 'Mobiles › Vivo'.
class SubcategoryOption {
  const SubcategoryOption({required this.id, required this.name, required this.path});

  factory SubcategoryOption.fromJson(Map<String, dynamic> json) => SubcategoryOption(
    id: json['id'].toString(),
    name: json['name'] as String,
    path: json['path'] as String,
  );

  final String id;
  final String name;
  final String path;
}

/// Everything the entry form needs, from `entry-options/`.
class EntryOptions {
  const EntryOptions({
    required this.categories,
    required this.paymentModes,
    required this.bankAccounts,
    required this.vendors,
    this.barcodeScanningEnabled = false,
    this.productSubcategories = const [],
    this.inventoryType = 'OTHER',
    this.attributeKeys = const ['brand', 'extra'],
    this.structure = CategoryStructure.standard,
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
      barcodeScanningEnabled: json['barcode_scanning_enabled'] == true,
      structure: json['structure'] == null
          ? CategoryStructure.standard
          : CategoryStructure.fromJson(json['structure'] as Map<String, dynamic>),
      inventoryType: (json['inventory_type'] as String?) ?? 'OTHER',
      attributeKeys: ((json['attribute_keys'] as List?) ?? const ['brand', 'extra'])
          .map((k) => k.toString())
          .toList(),
      productSubcategories: ((json['product_subcategories'] as List?) ?? const [])
          .map((s) => SubcategoryOption.fromJson(s as Map<String, dynamic>))
          .toList(),
    );
  }

  final Map<EntryType, List<EntryCategory>> categories;
  final List<PaymentModeOption> paymentModes;
  final List<NamedItem> bankAccounts;
  final List<String> vendors;

  /// Turned on per organization by a superadmin; the Scan & bill screen is hidden when off.
  final bool barcodeScanningEnabled;

  /// Every sub-category a product can be filed under (Mobiles › Vivo and so on).
  final List<SubcategoryOption> productSubcategories;

  /// 'MOBILES' (each phone tracked by IMEI), 'CLOTHES' or 'OTHER'; set per organization by a superadmin.
  final String inventoryType;

  /// The fields an item carries for this inventory type, e.g. brand, colour, ram, storage.
  final List<String> attributeKeys;

  /// The category levels this organization uses (set by a superadmin), 3 by default.
  final CategoryStructure structure;

  bool get tracksPhones => inventoryType == 'MOBILES';
}
