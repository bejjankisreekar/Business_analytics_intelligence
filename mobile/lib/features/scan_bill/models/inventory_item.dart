/// One item in the inventory: how many were bought and sold, and how many are left.
class InventoryItem {
  const InventoryItem({
    required this.name,
    required this.barcode,
    required this.path,
    required this.tracked,
    this.attributes = '',
    required this.purchased,
    required this.sold,
    required this.inStock,
    required this.stockValue,
  });

  factory InventoryItem.fromJson(Map<String, dynamic> json) => InventoryItem(
    name: json['name'] as String,
    barcode: (json['barcode'] as String?) ?? '',
    path: (json['path'] as String?) ?? '',
    tracked: json['tracked'] == true,
    attributes: (json['attributes'] as String?) ?? '',
    purchased: json['purchased'] as int,
    sold: json['sold'] as int,
    inStock: json['in_stock'] as int,
    stockValue: double.tryParse('${json['stock_value']}') ?? 0,
  );

  final String name;
  final String barcode;

  /// 'Mobiles › Vivo'; '' when the item has no sub-category.
  final String path;

  /// A phone model counted by IMEI.
  final bool tracked;

  /// 'Apple · Black · RAM 6GB · 128GB' or 'Raymond · Size M · Navy'.
  final String attributes;
  final int purchased;
  final int sold;
  final int inStock;

  /// In-stock quantity at the item's purchase price.
  final double stockValue;
}

/// The inventory list plus its totals.
class InventoryPage {
  const InventoryPage({
    required this.items,
    required this.purchased,
    required this.sold,
    required this.inStock,
    required this.stockValue,
  });

  factory InventoryPage.fromJson(Map<String, dynamic> json) {
    final totals = json['totals'] as Map<String, dynamic>;
    return InventoryPage(
      items: (json['items'] as List)
          .map((i) => InventoryItem.fromJson(i as Map<String, dynamic>))
          .toList(),
      purchased: totals['purchased'] as int,
      sold: totals['sold'] as int,
      inStock: totals['in_stock'] as int,
      stockValue: double.tryParse('${totals['stock_value']}') ?? 0,
    );
  }

  final List<InventoryItem> items;
  final int purchased;
  final int sold;
  final int inStock;
  final double stockValue;
}
