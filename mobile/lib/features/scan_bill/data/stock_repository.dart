import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';
import '../models/inventory_item.dart';
import '../models/scanned_product.dart';
import '../models/stock_unit.dart';

/// Phone stock and the item search/edit calls the scan screen and the stock screen use.
class StockRepository {
  StockRepository(this._api);

  final ApiClient _api;

  /// The phones recorded by IMEI. [status] is 'IN_STOCK', 'SOLD' or '' for all.
  Future<StockPage> units({String status = '', String query = ''}) async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(
        ApiEndpoints.stock,
        queryParameters: {
          if (status.isNotEmpty) 'status': status,
          if (query.isNotEmpty) 'q': query,
        },
      ),
    );
    return StockPage.fromJson(response.data!);
  }

  /// Every item with what was bought, sold and is left in stock, plus the totals.
  Future<InventoryPage> inventory() async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(ApiEndpoints.inventory),
    );
    return InventoryPage.fromJson(response.data!);
  }

  /// Saved products whose name contains [query] (at least two letters).
  Future<List<ScannedProduct>> searchProducts(String query) async {
    final response = await _api.send(
      (dio) => dio.get<Map<String, dynamic>>(
        ApiEndpoints.productSearch,
        queryParameters: {'q': query},
      ),
    );
    return (response.data!['products'] as List)
        .map((p) => ScannedProduct.fromJson(p as Map<String, dynamic>))
        .toList();
  }

  /// Saves an edited item. Throws an ApiException whose `fieldErrors` name the invalid inputs.
  Future<ScannedProduct> updateProduct({
    required String id,
    required String name,
    required String barcode,
    required String salePrice,
    required String purchasePrice,
    required List<String> path,
    Map<String, String> common = const {},
  }) async {
    final response = await _api.send(
      (dio) => dio.post<Map<String, dynamic>>(
        ApiEndpoints.productEdit(id),
        data: {
          'name': name,
          'barcode': barcode,
          'sale_price': salePrice,
          'purchase_price': purchasePrice,
          'path': path,
          'common': common,
        },
      ),
    );
    return ScannedProduct.fromJson(
      response.data!['product'] as Map<String, dynamic>,
    );
  }
}
