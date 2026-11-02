import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/providers.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/app_button.dart';
import '../../../shared/widgets/entry_line_tile.dart';
import '../../../shared/widgets/error_view.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../entries/application/entries_providers.dart';
import '../../entries/models/entry_options.dart';
import '../../entries/models/entry_type.dart';
import '../../entries/presentation/widgets/entry_form_widgets.dart';
import '../models/scanned_product.dart';
import 'full_screen_scanner.dart';

/// Scan items with the camera (or type a barcode), build one bill and save it as
/// sales or purchase rows. Each item is saved as its own row; the rows share a bill number.
class ScanBillScreen extends ConsumerStatefulWidget {
  const ScanBillScreen({super.key, this.openCamera = false});

  /// Start with the camera already scanning (used by the Daily screen's scan button).
  final bool openCamera;

  @override
  ConsumerState<ScanBillScreen> createState() => _ScanBillScreenState();
}

class _ScanBillScreenState extends ConsumerState<ScanBillScreen> {
  final _barcode = TextEditingController();

  EntryType _type = EntryType.sale;
  DateTime _date = DateTime.now();
  String? _categoryId;
  String? _subcategoryId;
  // Purchase: the model whose phones' IMEIs are being scanned next.
  ScannedProduct? _pendingProduct;

  // Name search: a product can be added without scanning it.
  final _search = TextEditingController();
  Timer? _searchTimer;
  List<ScannedProduct> _results = const [];
  String _paymentMode = 'CASH';
  String? _bankId;
  String? _vendor;
  final List<BillLine> _lines = [];

  // True while a lookup or the new-product sheet is running, so one scan is handled once.
  bool _busy = false;
  bool _saving = false;
  String _status = '';
  bool _statusIsError = false;
  Map<String, String> _errors = const {};

  @override
  void initState() {
    super.initState();
    if (widget.openCamera) {
      // Opened from the camera button: go straight to the scanner.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _openScanner();
      });
    }
  }

  @override
  void dispose() {
    _searchTimer?.cancel();
    _search.dispose();
    _barcode.dispose();
    super.dispose();
  }

  void _setStatus(String text, {bool error = false}) {
    setState(() {
      _status = text;
      _statusIsError = error;
    });
  }

  void _changeType(EntryType type) {
    setState(() {
      _type = type;
      _categoryId = null;
      _subcategoryId = null;
      _vendor = null;
      _errors = const {};
      // Sale and purchase prices differ, so a half-built bill of the other type is cleared.
      _lines.clear();
      _status = '';
    });
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(2000),
      lastDate: DateTime.now().add(const Duration(days: 1)),
    );
    if (picked != null) setState(() => _date = picked);
  }

  void _onSearchChanged(String text) {
    _searchTimer?.cancel();
    _searchTimer = Timer(const Duration(milliseconds: 250), () => _runSearch(text.trim()));
  }

  Future<void> _runSearch(String query) async {
    if (query.length < 2) {
      if (mounted) setState(() => _results = const []);
      return;
    }
    try {
      final found = await ref.read(stockRepositoryProvider).searchProducts(query);
      if (mounted) setState(() => _results = found);
    } on ApiException catch (error) {
      if (mounted) _setStatus(error.message, error: true);
    }
  }

  /// Opens the editor for the item on a bill line, then updates the line with the saved item.
  Future<void> _editLine(BillLine line) async {
    final saved = await showModalBottomSheet<ScannedProduct>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _EditItemSheet(
        product: line.product,
        structure: _structure(),
      ),
    );
    if (saved == null || !mounted) return;
    setState(() {
      line.product = saved;
      line.unitPrice = _type == EntryType.sale ? saved.salePrice : saved.purchasePrice;
    });
    _setStatus('Saved ${saved.name}.');
  }

  /// Opens the full-screen camera. Each barcode it reads is looked up and added to this bill,
  /// so when the scanner is closed the bill is already filled in.
  Future<void> _openScanner() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => FullScreenScanner(
          title: _type == EntryType.sale ? 'Scan to sell' : 'Scan to purchase',
          onScan: _scanFromCamera,
        ),
      ),
    );
  }

  Future<ScanOutcome> _scanFromCamera(String code) async {
    await _handleBarcode(code);
    final count = _lines.fold<int>(0, (sum, line) => sum + line.quantity);
    return (message: _status, error: _statusIsError, count: count);
  }

  void _submitTyped() {
    final code = _barcode.text.trim();
    _barcode.clear();
    _handleBarcode(code);
  }

  Future<void> _handleBarcode(String raw) async {
    final code = raw.trim();
    if (code.isEmpty || _busy) return;
    setState(() => _busy = true);
    _setStatus('Looking up $code…');
    try {
      final repo = ref.read(scanBillRepositoryProvider);
      final found = await repo.lookup(code);
      if (!mounted) return;
      if (found != null) {
        _applyLookup(found);
        return;
      }
      if (_phonesOn && looksLikeImei(code)) {
        if (_type == EntryType.purchase && _pendingProduct != null) {
          _addSerial(_pendingProduct!, code.toUpperCase(), _pendingProduct!.purchasePrice);
        } else {
          _setStatus('No phone with IMEI $code is in stock.', error: true);
        }
        return;
      }
      final created = await _askNewProduct(code);
      if (!mounted) return;
      if (created != null) {
        _addProduct(created);
      } else {
        _setStatus('No product added for $code.', error: true);
      }
    } on ApiException catch (error) {
      if (mounted) _setStatus(error.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// A product is added to the bill; a category or sub-category label sets the bill's
  /// category (and sub-category) instead.
  void _applyLookup(ScanLookup found) {
    if (found.type == 'product') {
      _addProduct(found.product!);
      return;
    }
    if (found.type == 'unit') {
      if (_type == EntryType.purchase) {
        _setStatus('IMEI ${found.serial} is already recorded in stock.', error: true);
      } else if (found.unitStatus != 'IN_STOCK') {
        _setStatus('IMEI ${found.serial} is already sold.', error: true);
      } else {
        _addSerial(
          found.product!,
          found.serial!,
          found.product!.salePrice,
          description: found.description,
        );
      }
      return;
    }
    final options = ref.read(entryOptionsProvider).valueOrNull;
    final categories = options?.categories[_type] ?? const <EntryCategory>[];
    if (found.type == 'category') {
      final match = categories.where((c) => c.id == found.id).firstOrNull;
      if (match == null) {
        _setStatus('${found.name} is not a category for this bill.', error: true);
        return;
      }
      setState(() {
        _categoryId = match.id;
        _subcategoryId = null;
      });
      _setStatus('Category set to ${match.name}.');
      return;
    }
    final parent = categories.where((c) => c.id == found.categoryId).firstOrNull;
    if (parent == null) {
      _setStatus('${found.name} is not for this bill.', error: true);
      return;
    }
    setState(() {
      _categoryId = parent.id;
      _subcategoryId = found.id;
    });
    _setStatus('Sub-category set to ${found.name}.');
  }

  /// Fills the bill's category and sub-category from the item, when it has a sub-category the
  /// bill can use and none is chosen yet. The staff can still change both.
  void _fillFromProduct(ScannedProduct product) {
    if (_subcategoryId != null || product.subcategoryId.isEmpty) return;
    final categories =
        ref.read(entryOptionsProvider).valueOrNull?.categories[_type] ??
        const <EntryCategory>[];
    for (final category in categories) {
      if (category.subcategories.any((s) => s.id == product.subcategoryId)) {
        _categoryId = category.id;
        _subcategoryId = product.subcategoryId;
        return;
      }
    }
  }

  void _addProduct(ScannedProduct product) {
    if (_type == EntryType.sale && product.tracked) {
      _setStatus(
        '${product.name} is tracked phone by phone. Scan the IMEI of the phone being sold.',
        error: true,
      );
      return;
    }
    if (_type == EntryType.purchase) _pendingProduct = product;
    final existing = _lines
        .where((line) => line.product.id == product.id && line.serial == null)
        .firstOrNull;
    setState(() {
      _fillFromProduct(product);
      if (existing != null) {
        existing.quantity += 1;
      } else {
        _lines.add(
          BillLine(
            product: product,
            unitPrice: _type == EntryType.sale
                ? product.salePrice
                : product.purchasePrice,
          ),
        );
      }
    });
    _setStatus('Added ${product.name}.');
  }

  /// A phone scanned by its IMEI: one line per phone. On a purchase the first IMEI
  /// replaces the untracked placeholder line that the box barcode added.
  /// What a new phone of this item starts with: its colour, RAM and storage from the item's common
  /// values, and its brand from the group the item is in.
  Map<String, String> _itemDefaults(ScannedProduct product) {
    final defaults = <String, String>{};
    product.common.forEach((name, value) {
      final key = name.toLowerCase();
      if (key == 'colour' || key == 'color') defaults['colour'] = value;
      if (key == 'ram') defaults['ram'] = value;
      if (key == 'storage') defaults['storage'] = value;
    });
    if (product.path.length > 1) defaults['brand'] = product.path.last;
    return defaults;
  }

  /// A new phone of the same model starts with the last one's colour, RAM and storage.
  Map<String, String> _lastDetails(ScannedProduct product) {
    final last = _lines
        .where((l) => l.product.id == product.id && l.serial != null)
        .lastOrNull;
    if (last == null) {
      return {
        ..._itemDefaults(product),
      };
    }
    return {
      for (final key in const ['colour', 'ram', 'storage'])
        if ((last.details[key] ?? '').isNotEmpty) key: last.details[key]!,
    };
  }

  Future<void> _editDetails(BillLine line) async {
    final saved = await showModalBottomSheet<Map<String, String>>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _PhoneDetailsSheet(
        product: line.product,
        initial: line.details,
      ),
    );
    if (saved != null && mounted) setState(() => line.details = saved);
  }

  void _addSerial(
    ScannedProduct product,
    String serial,
    double price, {
    String description = '',
  }) {
    if (_lines.any((line) => line.serial == serial)) {
      _setStatus('IMEI $serial is already on this bill.', error: true);
      return;
    }
    setState(() {
      _fillFromProduct(product);
      if (_type == EntryType.purchase) {
        _lines.removeWhere(
          (line) => line.product.id == product.id && line.serial == null,
        );
      }
      _lines.add(
        BillLine(
          product: product,
          unitPrice: price,
          serial: serial,
          details: _type == EntryType.purchase ? _lastDetails(product) : null,
          description: description,
        ),
      );
    });
    _setStatus('Added ${product.name} · IMEI $serial.');
  }

  bool looksLikeImei(String code) =>
      RegExp(r'^[0-9A-Za-z-]{14,20}$').hasMatch(code);

  /// Only a mobiles organization tracks each phone by its IMEI.
  bool get _phonesOn => ref.read(entryOptionsProvider).valueOrNull?.tracksPhones ?? false;

  /// The fields an item carries for this organization's inventory type.
  /// The category levels this organization uses.
  CategoryStructure _structure() =>
      ref.read(entryOptionsProvider).valueOrNull?.structure ?? CategoryStructure.standard;

  Future<ScannedProduct?> _askNewProduct(String barcode) {
    return showModalBottomSheet<ScannedProduct>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _NewProductSheet(
        barcode: barcode,
        structure: _structure(),
      ),
    );
  }

  Future<void> _save() async {
    if (_lines.isEmpty) {
      _setStatus('Scan at least one item before saving.', error: true);
      return;
    }
    setState(() {
      _saving = true;
      _errors = const {};
    });
    final payload = <String, dynamic>{
      'type': _type.apiValue,
      'date': isoDate(_date),
      'category': _categoryId,
      if (_subcategoryId != null) 'subcategory': _subcategoryId,
      'payment_mode': _paymentMode,
      if (_paymentMode == PaymentModeOption.bank && _bankId != null)
        'bank_account': _bankId,
      if (_type == EntryType.purchase && _vendor != null) 'vendor': _vendor,
      'lines': [
        for (final line in _lines)
          {
            'product_id': line.product.id,
            'quantity': line.quantity,
            'unit_price': line.unitPrice.toStringAsFixed(2),
            'serials': [if (line.serial != null) line.serial],
            if (line.serial != null && _type == EntryType.purchase)
              'details': line.details,
          },
      ],
    };

    try {
      final result = await ref.read(scanBillRepositoryProvider).saveBill(payload);
      if (!mounted) return;
      ref.invalidate(dayEntriesProvider(isoDate(_date)));
      setState(() {
        _lines.clear();
        _status = '';
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Saved ${result['bill_no']} · ${result['items']} item(s) · ₹ ${result['total']}',
          ),
        ),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.fieldErrors.isNotEmpty) {
        setState(() => _errors = error.fieldErrors);
      }
      _setStatus(
        error.fieldErrors.values.firstOrNull ?? error.message,
        error: true,
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final options = ref.watch(entryOptionsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Scan & bill'),
        actions: [
          IconButton(
            tooltip: 'Inventory & stock',
            icon: const Icon(Icons.inventory_2_outlined),
            onPressed: () => context.push(AppRoutes.stock),
          ),
          IconButton(
            tooltip: 'Print barcode labels',
            icon: const Icon(Icons.print_outlined),
            onPressed: () => context.push(AppRoutes.barcodeLabels),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 48),
        children: [
          TabPills<EntryType>(
            selected: _type,
            onChanged: _changeType,
            items: [
              for (final type in const [EntryType.sale, EntryType.purchase])
                TabPillItem(
                  value: type,
                  label: type.label,
                  icon: entryIcon(type),
                  color: type.color,
                ),
            ],
          ),
          DateStrip(
            date: _date,
            error: _errors['date'],
            accent: _type.color,
            onChanged: (d) => setState(() => _date = d),
            onPick: _pickDate,
          ),
          FormCard(
            title: 'Scan items',
            icon: Icons.qr_code_scanner,
            children: [
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _barcode,
                      textInputAction: TextInputAction.done,
                      decoration: InputDecoration(
                        hintText: 'Type or scan a barcode',
                        prefixIcon: const Icon(Icons.qr_code),
                        suffixIcon: IconButton(
                          icon: const Icon(Icons.keyboard_return),
                          tooltip: 'Add item',
                          onPressed: _submitTyped,
                        ),
                      ),
                      onSubmitted: (_) => _submitTyped(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filledTonal(
                    tooltip: 'Scan with camera',
                    icon: const Icon(Icons.photo_camera),
                    onPressed: _openScanner,
                  ),
                ],
              ),
              if (_status.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: Text(
                    _status,
                    style: TextStyle(
                      fontSize: 13,
                      color: _statusIsError
                          ? AppColors.danger
                          : AppColors.textSecondary,
                    ),
                  ),
                ),
            ],
          ),
          FormCard(
            title: 'Find by name',
            icon: Icons.search,
            children: [
              TextField(
                controller: _search,
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(
                  hintText: 'Type at least 2 letters of the product name',
                  prefixIcon: Icon(Icons.search),
                ),
                onChanged: _onSearchChanged,
              ),
              for (final product in _results)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(product.name),
                  subtitle: Text(
                    '₹ ${(_type == EntryType.sale ? product.salePrice : product.purchasePrice).toStringAsFixed(2)}'
                    '${product.subcategoryPath.isEmpty ? '' : ' · ${product.subcategoryPath}'}'
                    '${product.tracked ? ' · tracked by IMEI' : ''}',
                  ),
                  trailing: const Icon(Icons.add_circle_outline),
                  onTap: () {
                    _addProduct(product);
                    _search.clear();
                    setState(() => _results = const []);
                  },
                ),
            ],
          ),
          ...options.when(
            loading: () => const [
              Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              ),
            ],
            error: (error, _) => [
              ErrorView(
                message: userMessage(error),
                onRetry: () => ref.invalidate(entryOptionsProvider),
              ),
            ],
            data: _billSections,
          ),
        ],
      ),
    );
  }

  List<Widget> _billSections(EntryOptions options) {
    final accent = _type.color;
    final categories = options.categories[_type] ?? const <EntryCategory>[];
    final total = _lines.fold<double>(0, (sum, line) => sum + line.total);
    final count = _lines.fold<int>(0, (sum, line) => sum + line.quantity);

    return [
      FormCard(
        title: 'Bill',
        icon: Icons.receipt_long_outlined,
        children: [
          if (_errors['lines'] != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                _errors['lines']!,
                style: const TextStyle(color: AppColors.danger, fontSize: 13),
              ),
            ),
          if (_lines.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'No items yet. Scan the first one.',
                textAlign: TextAlign.center,
                style: TextStyle(color: AppColors.textSecondary),
              ),
            ),
          for (final line in _lines)
            _LineTile(
              key: ObjectKey(line),
              line: line,
              accent: accent,
              onChanged: () => setState(() {}),
              onEdit: () => _editLine(line),
              priceLabel: _type == EntryType.sale ? 'Selling price' : 'Purchase price',
              onDetails: _type == EntryType.purchase && line.serial != null
                  ? () => _editDetails(line)
                  : null,
              onRemove: () => setState(() => _lines.remove(line)),
            ),
          if (_lines.isNotEmpty) ...[
            const Divider(height: 24),
            Row(
              children: [
                Text(
                  'Items $count',
                  style: const TextStyle(color: AppColors.textSecondary),
                ),
                const Spacer(),
                Text(
                  '₹ ${total.toStringAsFixed(2)}',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                    color: accent,
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
      FormCard(
        title: 'Payment',
        icon: Icons.payments_outlined,
        children: [
          PickerField(
            label: _type == EntryType.sale ? 'Revenue channel' : 'Category',
            icon: Icons.category_outlined,
            accent: accent,
            options: {for (final c in categories) c.id: c.name},
            selected: _categoryId,
            placeholder: 'Choose one',
            emptyHint: 'None yet. Add them in the web app under Categories.',
            error: _errors[_type.categoryField] ?? _errors['category'],
            onChanged: (id) => setState(() {
              _categoryId = id;
              _subcategoryId = null;
            }),
          ),
          if (_categoryId != null &&
              (categories
                      .where((c) => c.id == _categoryId)
                      .firstOrNull
                      ?.subcategories
                      .isNotEmpty ??
                  false)) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Sub-category (optional)',
              icon: Icons.account_tree_outlined,
              accent: accent,
              clearable: true,
              options: {
                for (final s in categories
                    .where((c) => c.id == _categoryId)
                    .first
                    .subcategories)
                  s.id: s.name,
              },
              selected: _subcategoryId,
              placeholder: 'Not specified',
              onChanged: (id) => setState(() => _subcategoryId = id),
            ),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final mode in options.paymentModes)
                ChoiceChip(
                  label: Text(mode.label),
                  selected: _paymentMode == mode.value,
                  selectedColor: accent.withValues(alpha: 0.18),
                  onSelected: (_) => setState(() {
                    _paymentMode = mode.value;
                    _bankId = null;
                  }),
                ),
            ],
          ),
          if (_paymentMode == PaymentModeOption.bank &&
              options.bankAccounts.isNotEmpty) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Bank',
              icon: Icons.account_balance_outlined,
              accent: accent,
              clearable: true,
              options: {for (final b in options.bankAccounts) b.id: b.name},
              selected: _bankId,
              placeholder: 'Choose a bank',
              onChanged: (id) => setState(() => _bankId = id),
            ),
          ],
          if (_type == EntryType.purchase && options.vendors.isNotEmpty) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Vendor (optional)',
              icon: Icons.storefront_outlined,
              accent: accent,
              clearable: true,
              options: {for (final v in options.vendors) v: v},
              selected: _vendor,
              placeholder: 'No vendor',
              onChanged: (v) => setState(() => _vendor = v),
            ),
          ],
        ],
      ),
      Padding(
        padding: const EdgeInsets.only(top: 18),
        child: AppButton(
          label: 'Save ${_type.label.toLowerCase()} bill',
          color: accent,
          loading: _saving,
          onPressed: _saving || _busy ? null : _save,
        ),
      ),
    ];
  }
}

/// One scanned item on the bill: editable price and quantity, plus remove.
class _LineTile extends StatelessWidget {
  const _LineTile({
    super.key,
    required this.line,
    required this.accent,
    required this.onChanged,
    required this.onEdit,
    required this.priceLabel,
    required this.onDetails,
    required this.onRemove,
  });

  final BillLine line;
  final Color accent;
  final VoidCallback onChanged;
  final VoidCallback onEdit;

  /// 'Selling price' on a sale, 'Purchase price' on a purchase.
  final String priceLabel;

  /// Purchase: opens the phone's details (colour, RAM and so on); null when there are none to edit.
  final VoidCallback? onDetails;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            line.product.name,
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                        TextButton(
                          style: TextButton.styleFrom(
                            padding: const EdgeInsets.symmetric(horizontal: 6),
                            minimumSize: const Size(0, 28),
                            visualDensity: VisualDensity.compact,
                          ),
                          onPressed: onEdit,
                          child: const Text('Edit item'),
                        ),
                      ],
                    ),
                    if (line.serial == null &&
                        (line.product.path.isNotEmpty || line.product.attributesText.isNotEmpty))
                      Text(
                        [
                          if (line.product.path.isNotEmpty) line.product.path.join(' › '),
                          if (line.product.attributesText.isNotEmpty) line.product.attributesText,
                        ].join(' · '),
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    if (line.serial != null)
                      Text(
                        'IMEI ${line.serial}',
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    if (line.serial != null &&
                        (line.description.isNotEmpty ||
                            line.detailsSummary.isNotEmpty))
                      Text(
                        line.description.isNotEmpty
                            ? line.description
                            : line.detailsSummary,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    if (onDetails != null)
                      TextButton(
                        style: TextButton.styleFrom(
                          padding: EdgeInsets.zero,
                          minimumSize: const Size(0, 28),
                          visualDensity: VisualDensity.compact,
                        ),
                        onPressed: onDetails,
                        child: const Text('Phone details'),
                      ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Remove item',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close, color: AppColors.textSecondary),
                onPressed: onRemove,
              ),
            ],
          ),
          Row(
            children: [
              SizedBox(
                width: 120,
                child: TextFormField(
                  initialValue: line.unitPrice.toStringAsFixed(2),
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: InputDecoration(
                    labelText: priceLabel,
                    prefixText: '₹ ',
                    isDense: true,
                  ),
                  onChanged: (value) {
                    line.unitPrice = double.tryParse(value.trim()) ?? 0;
                    onChanged();
                  },
                ),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'One less',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.remove_circle_outline),
                onPressed: line.serial == null && line.quantity > 1
                    ? () {
                        line.quantity -= 1;
                        onChanged();
                      }
                    : null,
              ),
              Text(
                '${line.quantity}',
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              IconButton(
                tooltip: 'One more',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.add_circle_outline),
                onPressed: line.serial != null
                    ? null
                    : () {
                        line.quantity += 1;
                        onChanged();
                      },
              ),
              SizedBox(
                width: 84,
                child: Text(
                  line.total.toStringAsFixed(2),
                  textAlign: TextAlign.end,
                  style: TextStyle(fontWeight: FontWeight.w800, color: accent),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Bottom sheet to save a product for a barcode the shop has not used before.
/// Pops with the saved product, or nothing if cancelled.
class _NewProductSheet extends ConsumerStatefulWidget {
  const _NewProductSheet({
    required this.barcode,
    required this.structure,
  });

  final String barcode;
  final CategoryStructure structure;

  @override
  ConsumerState<_NewProductSheet> createState() => _NewProductSheetState();
}

class _NewProductSheetState extends ConsumerState<_NewProductSheet> {
  final _name = TextEditingController();
  final _sale = TextEditingController();
  final _purchase = TextEditingController();
  bool _saving = false;
  String? _error;
  late final _fields = _StructureFields(widget.structure, null);

  @override
  void dispose() {
    _name.dispose();
    _sale.dispose();
    _purchase.dispose();
    _fields.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => _error = 'Enter a product name.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final product = await ref
          .read(scanBillRepositoryProvider)
          .createProduct(
            barcode: widget.barcode,
            name: name,
            salePrice: _sale.text.trim().isEmpty ? '0' : _sale.text.trim(),
            purchasePrice: _purchase.text.trim().isEmpty
                ? '0'
                : _purchase.text.trim(),
            path: _fields.path(),
            common: _fields.common(),
          );
      if (mounted) Navigator.of(context).pop(product);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.fieldErrors.values.firstOrNull ?? error.message);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        0,
        20,
        24 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            'New product',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            'Barcode ${widget.barcode}',
            style: const TextStyle(color: AppColors.textSecondary),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _name,
            autofocus: true,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(
              labelText: widget.structure.item,
              hintText: 'e.g. Cotton kurta, M',
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _sale,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Sale price',
                    prefixText: '₹ ',
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _purchase,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Purchase price',
                    prefixText: '₹ ',
                  ),
                ),
              ),
            ],
          ),
          ..._fields.fields(),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: const TextStyle(color: AppColors.danger)),
          ],
          const SizedBox(height: 16),
          AppButton(
            label: 'Save product & add',
            loading: _saving,
            onPressed: _saving ? null : _save,
          ),
        ],
      ),
    );
  }
}

/// Edits a saved product's name, code and prices. Pops with the saved product.
class _EditItemSheet extends ConsumerStatefulWidget {
  const _EditItemSheet({
    required this.product,
    required this.structure,
  });

  final ScannedProduct product;
  final CategoryStructure structure;

  @override
  ConsumerState<_EditItemSheet> createState() => _EditItemSheetState();
}

class _EditItemSheetState extends ConsumerState<_EditItemSheet> {

  late final _name = TextEditingController(text: widget.product.name);
  late final _barcode = TextEditingController(text: widget.product.barcode);
  late final _sale = TextEditingController(
    text: widget.product.salePrice.toStringAsFixed(2),
  );
  late final _purchase = TextEditingController(
    text: widget.product.purchasePrice.toStringAsFixed(2),
  );
  bool _saving = false;
  String? _error;
  late final _fields = _StructureFields(widget.structure, widget.product);

  @override
  void dispose() {
    _name.dispose();
    _barcode.dispose();
    _sale.dispose();
    _purchase.dispose();
    _fields.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final saved = await ref.read(stockRepositoryProvider).updateProduct(
        id: widget.product.id,
        name: _name.text.trim(),
        barcode: _barcode.text.trim(),
        salePrice: _sale.text.trim().isEmpty ? '0' : _sale.text.trim(),
        purchasePrice: _purchase.text.trim().isEmpty ? '0' : _purchase.text.trim(),
        path: _fields.path(),
            common: _fields.common(),
      );
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (error) {
      if (mounted) {
        setState(() => _error = error.fieldErrors.values.firstOrNull ?? error.message);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        0,
        20,
        24 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            'Edit item',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          const Text(
            'Changes the saved item, so the next bill uses it too.',
            style: TextStyle(color: AppColors.textSecondary),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _name,
            decoration: InputDecoration(labelText: widget.structure.item),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _barcode,
            decoration: const InputDecoration(labelText: 'Barcode'),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _sale,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(
                    labelText: 'Sale price',
                    prefixText: '₹ ',
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _purchase,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  decoration: const InputDecoration(
                    labelText: 'Purchase price',
                    prefixText: '₹ ',
                  ),
                ),
              ),
            ],
          ),
          ..._fields.fields(),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: const TextStyle(color: AppColors.danger)),
          ],
          const SizedBox(height: 16),
          AppButton(
            label: 'Save item',
            loading: _saving,
            onPressed: _saving ? null : _save,
          ),
        ],
      ),
    );
  }
}

/// What this phone is: brand, model, colour, RAM, storage, a second IMEI and anything else.
/// Pops with the filled-in details.
class _PhoneDetailsSheet extends StatefulWidget {
  const _PhoneDetailsSheet({required this.product, required this.initial});

  final ScannedProduct product;
  final Map<String, String> initial;

  @override
  State<_PhoneDetailsSheet> createState() => _PhoneDetailsSheetState();
}

class _PhoneDetailsSheetState extends State<_PhoneDetailsSheet> {
  static const _fields = <(String, String, TextInputType)>[
    ('brand', 'Brand', TextInputType.text),
    ('model', 'Model', TextInputType.text),
    ('colour', 'Colour', TextInputType.text),
    ('ram', 'RAM (e.g. 8GB)', TextInputType.text),
    ('storage', 'Storage (e.g. 128GB)', TextInputType.text),
    ('imei2', 'Second IMEI (dual SIM)', TextInputType.number),
    ('other', 'Other details', TextInputType.text),
  ];

  late final Map<String, TextEditingController> _controllers = {
    for (final f in _fields)
      f.$1: TextEditingController(text: widget.initial[f.$1] ?? ''),
  };

  @override
  void initState() {
    super.initState();
    // Brand and model start from the item, unless they were already typed.
    final path = widget.product.subcategoryPath;
    if (_controllers['brand']!.text.isEmpty && path.isNotEmpty) {
      _controllers['brand']!.text = path.split(' › ').last;
    }
    if (_controllers['model']!.text.isEmpty) {
      _controllers['model']!.text = widget.product.name;
    }
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _save() {
    Navigator.of(context).pop({
      for (final entry in _controllers.entries)
        if (entry.value.text.trim().isNotEmpty) entry.key: entry.value.text.trim(),
    });
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        0,
        20,
        24 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Phone details',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'Saved with this phone when the purchase is saved.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: 16),
            for (final f in _fields) ...[
              TextField(
                controller: _controllers[f.$1],
                keyboardType: f.$3,
                textCapitalization: TextCapitalization.words,
                decoration: InputDecoration(labelText: f.$2),
              ),
              const SizedBox(height: 12),
            ],
            const SizedBox(height: 4),
            AppButton(label: 'Save details', onPressed: _save),
          ],
        ),
      ),
    );
  }
}

/// Starter suggestions for common levels people usually have; the shop's own saved values come first.
const _starterValues = <String, List<String>>{
  'colour': ['Black', 'White', 'Blue', 'Red', 'Green', 'Grey', 'Silver', 'Gold', 'Navy'],
  'ram': ['2GB', '3GB', '4GB', '6GB', '8GB', '12GB', '16GB'],
  'storage': ['32GB', '64GB', '128GB', '256GB', '512GB', '1TB'],
  'size': ['XS', 'S', 'M', 'L', 'XL', 'XXL', 'Free size'],
  'brand': ['Apple', 'Samsung', 'Xiaomi', 'Redmi', 'Vivo', 'Oppo', 'Realme', 'OnePlus', 'Motorola'],
};

/// One box for each group level above the item, then one for each common level (optional),
/// each with tap-to-fill suggestions. Used by the add and edit sheets.
class _StructureFields {
  _StructureFields(this.structure, ScannedProduct? product) {
    for (var i = 0; i < structure.groups.length; i++) {
      final text = (product != null && i < product.path.length) ? product.path[i] : '';
      _groups.add(TextEditingController(text: text));
    }
    for (final name in structure.commons) {
      _commons[name] = TextEditingController(text: product?.common[name] ?? '');
    }
  }

  final CategoryStructure structure;
  final List<TextEditingController> _groups = [];
  final Map<String, TextEditingController> _commons = {};

  List<Widget> fields() => [
    for (var i = 0; i < _groups.length; i++)
      ..._field(
        structure.groups[i],
        _groups[i],
        i < structure.pathOptions.length ? structure.pathOptions[i] : const [],
      ),
    for (final entry in _commons.entries)
      ..._field('${entry.key} (optional)', entry.value, _commonOptions(entry.key)),
  ];

  List<String> _commonOptions(String name) {
    final saved = structure.commonOptions[name] ?? const <String>[];
    final starter = (_starterValues[name.toLowerCase()] ?? const <String>[])
        .where((v) => !saved.contains(v));
    return [...saved, ...starter];
  }

  List<Widget> _field(String label, TextEditingController controller, List<String> options) => [
    TextField(
      controller: controller,
      textCapitalization: TextCapitalization.words,
      decoration: InputDecoration(labelText: label),
    ),
    _Suggestions(controller: controller, options: options),
    const SizedBox(height: 12),
  ];

  /// The groups typed, top first, with blanks at the end dropped.
  List<String> path() {
    final names = _groups.map((c) => c.text.trim()).toList();
    while (names.isNotEmpty && names.last.isEmpty) {
      names.removeLast();
    }
    return names;
  }

  Map<String, String> common() => {
    for (final entry in _commons.entries) entry.key: entry.value.text.trim(),
  };

  void dispose() {
    for (final controller in [..._groups, ..._commons.values]) {
      controller.dispose();
    }
  }
}

/// A row of tap-to-fill suggestions under a box, narrowed to what has been typed so far.
class _Suggestions extends StatelessWidget {
  const _Suggestions({required this.controller, required this.options});

  final TextEditingController controller;
  final List<String> options;

  @override
  Widget build(BuildContext context) {
    if (options.isEmpty) return const SizedBox.shrink();
    return ValueListenableBuilder<TextEditingValue>(
      valueListenable: controller,
      builder: (context, value, _) {
        final typed = value.text.trim().toLowerCase();
        final shown = options
            .where((o) => o.toLowerCase().contains(typed) && o.toLowerCase() != typed)
            .take(6)
            .toList();
        if (shown.isEmpty) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Wrap(
            spacing: 6,
            runSpacing: 0,
            children: [
              for (final option in shown)
                ActionChip(
                  visualDensity: VisualDensity.compact,
                  label: Text(option, style: const TextStyle(fontSize: 12)),
                  onPressed: () => controller.text = option,
                ),
            ],
          ),
        );
      },
    );
  }
}
