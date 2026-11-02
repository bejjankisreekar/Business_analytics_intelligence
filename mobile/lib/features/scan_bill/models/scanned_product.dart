/// A product saved with a barcode, as returned by `products/` and `products/lookup/`.
class ScannedProduct {
  const ScannedProduct({
    required this.id,
    required this.barcode,
    required this.name,
    required this.salePrice,
    required this.purchasePrice,
    this.tracked = false,
    this.subcategoryId = '',
    this.subcategoryPath = '',
    this.brand = '',
    this.size = '',
    this.colour = '',
    this.ram = '',
    this.storage = '',
    this.extra = '',
    this.attributesText = '',
    this.path = const [],
    this.common = const {},
  });

  factory ScannedProduct.fromJson(Map<String, dynamic> json) => ScannedProduct(
    id: json['id'].toString(),
    barcode: json['barcode'] as String,
    name: json['name'] as String,
    salePrice: double.tryParse('${json['sale_price']}') ?? 0,
    purchasePrice: double.tryParse('${json['purchase_price']}') ?? 0,
    tracked: json['tracked'] == true,
    subcategoryId: (json['subcategory_id'] as String?) ?? '',
    subcategoryPath: (json['subcategory_path'] as String?) ?? '',
    brand: (json['brand'] as String?) ?? '',
    size: (json['size'] as String?) ?? '',
    colour: (json['colour'] as String?) ?? '',
    ram: (json['ram'] as String?) ?? '',
    storage: (json['storage'] as String?) ?? '',
    extra: (json['extra'] as String?) ?? '',
    attributesText: (json['attributes_text'] as String?) ?? '',
    path: ((json['path'] as List?) ?? const []).map((p) => p.toString()).toList(),
    common: {
      for (final e in ((json['common'] as Map?) ?? const {}).entries)
        e.key.toString(): e.value.toString(),
    },
  );

  final String id;
  final String barcode;
  final String name;
  final double salePrice;
  final double purchasePrice;

  /// Phones of this model are sold one by one, by their IMEI.
  final bool tracked;

  /// The sub-category the item is filed under; '' when it has none.
  final String subcategoryId;

  /// 'Mobiles › Vivo'; '' when the item has no sub-category.
  final String subcategoryPath;

  /// What the item is beyond its name: brand, size, colour, RAM, storage, other details.
  final String brand;
  final String size;
  final String colour;
  final String ram;
  final String storage;
  final String extra;

  /// 'Apple · Black · RAM 6GB · 128GB' (or 'Raymond · Size M · Navy'); '' when none is recorded.
  final String attributesText;

  /// The groups the item sits in, top first, e.g. ['Mobile', 'Apple'].
  final List<String> path;

  /// Values from the common levels, by level name, e.g. {'Colour': 'Black', 'RAM': '8GB'}.
  final Map<String, String> common;

  Map<String, String> get attributes => {
    'brand': brand,
    'size': size,
    'colour': colour,
    'ram': ram,
    'storage': storage,
    'extra': extra,
  };
}

/// What a scanned code stands for: a product, a category or a sub-category.
class ScanLookup {
  const ScanLookup({
    required this.type,
    this.product,
    this.id,
    this.name = '',
    this.categoryId,
    this.serial,
    this.unitStatus,
    this.description = '',
  });

  factory ScanLookup.fromJson(Map<String, dynamic> json) {
    final type = json['type'] as String? ?? 'product';
    if (type == 'unit') {
      final unit = json['unit'] as Map<String, dynamic>;
      return ScanLookup(
        type: type,
        serial: unit['serial'] as String,
        unitStatus: unit['status'] as String,
        description: (unit['description'] as String?) ?? '',
        product: ScannedProduct.fromJson(unit['product'] as Map<String, dynamic>),
      );
    }
    if (type == 'category') {
      final category = json['category'] as Map<String, dynamic>;
      return ScanLookup(
        type: type,
        id: category['id'].toString(),
        name: category['name'] as String,
      );
    }
    if (type == 'subcategory') {
      final sub = json['subcategory'] as Map<String, dynamic>;
      return ScanLookup(
        type: type,
        id: sub['id'].toString(),
        name: sub['name'] as String,
        categoryId: sub['category_id'].toString(),
      );
    }
    return ScanLookup(
      type: 'product',
      product: ScannedProduct.fromJson(json['product'] as Map<String, dynamic>),
    );
  }

  /// 'product', 'category' or 'subcategory'.
  final String type;
  final ScannedProduct? product;

  /// Id of the category or sub-category; null for a product.
  final String? id;
  final String name;

  /// For a sub-category, the id of the category it sits under.
  final String? categoryId;

  /// For a phone (type 'unit'): its IMEI or serial, and IN_STOCK or SOLD.
  final String? serial;
  final String? unitStatus;

  /// What the phone was recorded as, e.g. 'Vivo · V10 · Blue · RAM 8GB · 128GB'.
  final String description;
}

/// One scanned item on the bill being built. `quantity` and `unitPrice` can be
/// changed on the screen before the bill is saved.
class BillLine {
  BillLine({
    required this.product,
    required this.unitPrice,
    this.quantity = 1,
    this.serial,
    Map<String, String>? details,
    this.description = '',
  }) : details = details ?? {};

  ScannedProduct product;
  int quantity;
  double unitPrice;

  /// IMEI or serial number of the phone on this line; null for an untracked item.
  final String? serial;

  /// Purchase: what the phone is (brand, model, colour, ram, storage, imei2, other).
  Map<String, String> details;

  /// Sale: what the scanned phone was recorded as.
  final String description;

  /// The details typed for a purchased phone, as one line of text.
  String get detailsSummary => [
    details['brand'],
    details['model'],
    details['colour'],
    if ((details['ram'] ?? '').isNotEmpty) 'RAM ${details['ram']}',
    details['storage'],
    if ((details['imei2'] ?? '').isNotEmpty) 'IMEI 2 ${details['imei2']}',
    details['other'],
  ].where((p) => p != null && p.isNotEmpty).join(' · ');

  double get total => quantity * unitPrice;
}
