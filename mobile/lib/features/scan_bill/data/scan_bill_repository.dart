import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../../../core/network/api_client.dart';
import '../../../core/network/api_endpoints.dart';
import '../../../core/network/api_exception.dart';
import '../models/scanned_product.dart';

class ScanBillRepository {
  ScanBillRepository(this._api);

  final ApiClient _api;

  /// What this code stands for (product, category or sub-category), or null when no item uses it.
  Future<ScanLookup?> lookup(String barcode) async {
    try {
      final response = await _api.send(
        (dio) => dio.get<Map<String, dynamic>>(
          ApiEndpoints.productLookup,
          queryParameters: {'barcode': barcode},
        ),
      );
      return ScanLookup.fromJson(response.data!);
    } on ApiException catch (error) {
      if (error.statusCode == 404) return null;
      rethrow;
    }
  }

  /// Saves a product for a barcode that was not known. Throws an ApiException whose
  /// `fieldErrors` name the invalid inputs (for example a duplicate barcode).
  Future<ScannedProduct> createProduct({
    required String barcode,
    required String name,
    required String salePrice,
    required String purchasePrice,
    List<String> path = const [],
    Map<String, String> common = const {},
  }) async {
    final response = await _api.send(
      (dio) => dio.post<Map<String, dynamic>>(
        ApiEndpoints.products,
        data: {
          'barcode': barcode,
          'name': name,
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

  /// The printable label sheet (A4 PDF) for one kind: 'product', 'category' or 'subcategory'.
  Future<Uint8List> labelsPdf(String kind) async {
    final response = await _api.send(
      (dio) => dio.get<List<int>>(
        ApiEndpoints.labelsPdf,
        queryParameters: {'kind': kind},
        options: Options(responseType: ResponseType.bytes),
      ),
    );
    return Uint8List.fromList(response.data!);
  }

  /// Saves the whole bill. Returns the server's summary: bill_no, items, total, date.
  Future<Map<String, dynamic>> saveBill(Map<String, dynamic> payload) async {
    final response = await _api.send(
      (dio) => dio.post<Map<String, dynamic>>(ApiEndpoints.bills, data: payload),
    );
    return response.data!;
  }
}
