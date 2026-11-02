/// One phone in stock or sold, as listed on the phone stock screen.
class StockUnit {
  const StockUnit({
    required this.serial,
    required this.status,
    required this.product,
    this.purchaseBillNo = '',
    this.purchaseDate,
    this.saleBillNo = '',
    this.saleDate,
    this.description = '',
    this.imei2 = '',
    this.other = '',
  });

  factory StockUnit.fromJson(Map<String, dynamic> json) => StockUnit(
    serial: json['serial'] as String,
    status: json['status'] as String,
    product: json['product'] as String,
    purchaseBillNo: (json['purchase_bill_no'] as String?) ?? '',
    purchaseDate: json['purchase_date'] as String?,
    saleBillNo: (json['sale_bill_no'] as String?) ?? '',
    saleDate: json['sale_date'] as String?,
    description: (json['description'] as String?) ?? '',
    imei2: (json['imei2'] as String?) ?? '',
    other: (json['other'] as String?) ?? '',
  );

  final String serial;

  /// 'IN_STOCK' or 'SOLD'.
  final String status;
  final String product;
  final String purchaseBillNo;
  final String? purchaseDate;
  final String saleBillNo;
  final String? saleDate;

  /// 'Vivo · V10 · Blue · RAM 8GB · 128GB'; empty for a phone recorded before details were kept.
  final String description;
  final String imei2;
  final String other;

  bool get sold => status == 'SOLD';
}

/// The stock list plus its counts.
class StockPage {
  const StockPage({required this.units, required this.inStock, required this.sold});

  factory StockPage.fromJson(Map<String, dynamic> json) => StockPage(
    units: (json['units'] as List)
        .map((u) => StockUnit.fromJson(u as Map<String, dynamic>))
        .toList(),
    inStock: json['in_stock'] as int,
    sold: json['sold'] as int,
  );

  final List<StockUnit> units;
  final int inStock;
  final int sold;
}
