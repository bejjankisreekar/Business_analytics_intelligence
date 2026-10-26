import 'package:flutter/material.dart';

import '../../../app/theme/app_colors.dart';

enum EntryType {
  sale('sale', 'Revenue', 'gross_amount', 'channel', AppColors.sale),
  expense('expense', 'Expense', 'amount', 'category', AppColors.expense),
  purchase('purchase', 'Purchase', 'amount', 'category', AppColors.purchase);

  const EntryType(
    this.apiValue,
    this.label,
    this.amountField,
    this.categoryField,
    this.color,
  );

  /// Value of `type` in API requests and responses.
  final String apiValue;
  final String label;

  /// Name of the amount field in the request (and in its validation errors).
  final String amountField;

  /// Name the backend's validation errors use for the category.
  final String categoryField;
  final Color color;

  /// Revenue adds to the day's net; expenses and purchases take away.
  int get sign => this == EntryType.sale ? 1 : -1;

  static EntryType fromApi(String value) => EntryType.values.firstWhere(
    (t) => t.apiValue == value,
    orElse: () => EntryType.sale,
  );
}
