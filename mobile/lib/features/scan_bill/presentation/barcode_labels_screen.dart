import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:printing/printing.dart';

import '../../../app/providers.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/network/api_exception.dart';
import '../../../shared/widgets/app_button.dart';
import '../../../shared/widgets/report_blueprint.dart';

/// Prints barcode label sheets for products, categories and sub-categories.
/// Each sheet is an A4 page of stickers; the phone's print dialog (or share sheet) takes it from there.
class BarcodeLabelsScreen extends ConsumerStatefulWidget {
  const BarcodeLabelsScreen({super.key});

  @override
  ConsumerState<BarcodeLabelsScreen> createState() =>
      _BarcodeLabelsScreenState();
}

class _BarcodeLabelsScreenState extends ConsumerState<BarcodeLabelsScreen> {
  String? _printing;

  Future<void> _print(String kind, String title) async {
    setState(() => _printing = kind);
    try {
      final bytes = await ref.read(scanBillRepositoryProvider).labelsPdf(kind);
      await Printing.layoutPdf(
        name: 'barcode-labels-$kind.pdf',
        onLayout: (_) async => bytes,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open the $title labels to print.')),
      );
    } finally {
      if (mounted) setState(() => _printing = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ReportPage(
      title: 'Barcode labels',
      onRefresh: () async {},
      children: [
        const Padding(
          padding: EdgeInsets.only(bottom: 16),
          child: Text(
            'Print labels for your products, categories and sub-categories, '
            'then stick them on the items and shelves. Use A4 sticker sheets, '
            '3 across and 8 down, and print at 100% scale.',
            style: TextStyle(color: AppColors.textSecondary, height: 1.4),
          ),
        ),
        for (final entry in const [
          ('product', 'Products', Icons.inventory_2_outlined),
          ('category', 'Categories', Icons.category_outlined),
          ('subcategory', 'Sub-categories', Icons.account_tree_outlined),
        ])
          Container(
            margin: const EdgeInsets.only(bottom: 12),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppColors.card,
              borderRadius: BorderRadius.circular(20),
              boxShadow: AppColors.cardShadow,
            ),
            child: Row(
              children: [
                Icon(entry.$3, color: AppColors.brand),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    entry.$2,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                AppButton(
                  label: 'Print',
                  loading: _printing == entry.$1,
                  onPressed: _printing != null
                      ? null
                      : () => _print(entry.$1, entry.$2),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
