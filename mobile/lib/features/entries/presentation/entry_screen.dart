import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/providers.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/app_button.dart';
import '../../../shared/widgets/entry_line_tile.dart';
import '../../../shared/widgets/error_view.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../application/entries_providers.dart';
import '../models/entry_options.dart';
import '../models/entry_type.dart';
import 'widgets/day_entries_list.dart';
import 'widgets/entry_form_widgets.dart';

/// Record a revenue, expense or purchase, and see what is already recorded for the day.
class EntryScreen extends ConsumerStatefulWidget {
  const EntryScreen({super.key});

  @override
  ConsumerState<EntryScreen> createState() => _EntryScreenState();
}

class _EntryScreenState extends ConsumerState<EntryScreen> {
  final _amount = TextEditingController();
  final _discount = TextEditingController();
  final _quantity = TextEditingController();
  final _note = TextEditingController();

  EntryType _type = EntryType.sale;
  DateTime _date = DateTime.now();
  String? _categoryId;
  String? _subcategoryId;
  String? _itemId;
  String? _vendor;
  String _paymentMode = 'CASH';
  String? _bankId;
  Map<String, String> _errors = const {};
  bool _saving = false;

  @override
  void dispose() {
    _amount.dispose();
    _discount.dispose();
    _quantity.dispose();
    _note.dispose();
    super.dispose();
  }

  void _changeType(EntryType type) {
    setState(() {
      _type = type;
      _categoryId = null;
      _subcategoryId = null;
      _itemId = null;
      _errors = const {};
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

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _errors = const {};
    });
    final payload = <String, dynamic>{
      'type': _type.apiValue,
      'date': isoDate(_date),
      'category': _categoryId,
      'subcategory': _itemId ?? _subcategoryId,
      _type.amountField: _amount.text.trim(),
      if (_type == EntryType.sale && _discount.text.trim().isNotEmpty)
        'discount': _discount.text.trim(),
      if (_type != EntryType.expense && _quantity.text.trim().isNotEmpty)
        'quantity': _quantity.text.trim(),
      if (_type == EntryType.purchase && _vendor != null) 'vendor': _vendor,
      'payment_mode': _paymentMode,
      if (_paymentMode == PaymentModeOption.bank && _bankId != null)
        'bank_account': _bankId,
      if (_note.text.trim().isNotEmpty) 'note': _note.text.trim(),
    };

    try {
      await ref.read(entriesRepositoryProvider).create(payload);
      _amount.clear();
      _discount.clear();
      _quantity.clear();
      _note.clear();
      setState(() {
        _subcategoryId = null;
        _itemId = null;
      });
      ref.invalidate(dayEntriesProvider(isoDate(_date)));
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('${_type.label} saved')));
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.fieldErrors.isNotEmpty) {
        setState(() => _errors = error.fieldErrors);
      } else {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final options = ref.watch(entryOptionsProvider);
    final date = isoDate(_date);

    return Scaffold(
      appBar: AppBar(title: const Text('New entry')),
      body: BrandedRefresh(
        onRefresh: () async {
          ref.invalidate(entryOptionsProvider);
          ref.invalidate(dayEntriesProvider(date));
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 48),
          children: [
            TabPills<EntryType>(
              selected: _type,
              onChanged: _changeType,
              items: [
                for (final type in EntryType.values)
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
              data: _form,
            ),
            const Padding(
              padding: EdgeInsets.only(top: 28, bottom: 10),
              child: Text(
                'Entries on this day',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
              ),
            ),
            DayEntriesList(date: date),
          ],
        ),
      ),
    );
  }

  List<Widget> _form(EntryOptions options) {
    final accent = _type.color;
    final categories = options.categories[_type] ?? const <EntryCategory>[];
    final category = categories.where((c) => c.id == _categoryId).firstOrNull;
    final subcategories = category?.subcategories ?? const <NamedItem>[];
    final subcategory = subcategories
        .where((s) => s.id == _subcategoryId)
        .firstOrNull;
    final items = subcategory?.children ?? const <NamedItem>[];

    final gross = num.tryParse(_amount.text.trim()) ?? 0;
    final discount = num.tryParse(_discount.text.trim()) ?? 0;
    final amountLabel = _type == EntryType.sale ? 'Gross amount' : 'Amount';

    return [
      FormCard(
        title: 'What is it for',
        icon: Icons.category_outlined,
        children: [
          PickerField(
            label: _type == EntryType.sale ? 'Revenue category' : 'Category',
            icon: Icons.category_outlined,
            accent: accent,
            options: {for (final c in categories) c.id: c.name},
            selected: _categoryId,
            placeholder: 'Choose a category',
            emptyHint:
                'No categories yet. Add them in the web app under Categories.',
            error: _errors[_type.categoryField] ?? _errors['category'],
            onChanged: (id) => setState(() {
              _categoryId = id;
              _subcategoryId = null;
              _itemId = null;
            }),
          ),
          if (subcategories.isNotEmpty) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Sub-category (optional)',
              icon: Icons.account_tree_outlined,
              accent: accent,
              clearable: true,
              options: {for (final s in subcategories) s.id: s.name},
              selected: _subcategoryId,
              placeholder: 'All / not specified',
              error: _errors['subcategory'],
              onChanged: (id) => setState(() {
                _subcategoryId = id;
                _itemId = null;
              }),
            ),
          ],
          if (items.isNotEmpty) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Item (optional)',
              icon: Icons.label_outline,
              accent: accent,
              clearable: true,
              options: {for (final i in items) i.id: i.name},
              selected: _itemId,
              placeholder: 'Not specified',
              onChanged: (id) => setState(() => _itemId = id),
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
              error: _errors['vendor'],
              onChanged: (v) => setState(() => _vendor = v),
            ),
          ],
        ],
      ),
      FormCard(
        title: 'Amount',
        icon: Icons.currency_rupee,
        children: [
          TextField(
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 34,
              fontWeight: FontWeight.w800,
              color: accent,
            ),
            decoration: InputDecoration(
              labelText: amountLabel,
              hintText: '0',
              prefixText: '₹ ',
              prefixStyle: TextStyle(
                fontSize: 34,
                fontWeight: FontWeight.w800,
                color: accent,
              ),
              errorText: _errors[_type.amountField],
              contentPadding: const EdgeInsets.symmetric(
                vertical: 18,
                horizontal: 16,
              ),
            ),
            onChanged: (_) => setState(() {}),
          ),
          if (_type != EntryType.expense) ...[
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (_type != EntryType.expense)
                  Expanded(
                    child: TextField(
                      controller: _quantity,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                        labelText: 'Quantity',
                        prefixIcon: const Icon(
                          Icons.inventory_2_outlined,
                          size: 20,
                        ),
                        errorText: _errors['quantity'],
                      ),
                    ),
                  ),
                if (_type == EntryType.sale) ...[
                  const SizedBox(width: 10),
                  Expanded(
                    child: TextField(
                      controller: _discount,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      decoration: InputDecoration(
                        labelText: 'Discount',
                        prefixIcon: const Icon(Icons.sell_outlined, size: 20),
                        errorText: _errors['discount'],
                      ),
                      onChanged: (_) => setState(() {}),
                    ),
                  ),
                ],
              ],
            ),
          ],
          if (_type == EntryType.sale && discount > 0 && gross > 0)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 10,
                ),
                decoration: BoxDecoration(
                  color: AppColors.brandSoft,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  children: [
                    const Expanded(
                      child: Text(
                        'Net revenue',
                        style: TextStyle(fontWeight: FontWeight.w600),
                      ),
                    ),
                    Text(
                      '₹${formatAmount(gross - discount)}',
                      style: TextStyle(
                        fontWeight: FontWeight.w800,
                        color: accent,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
      FormCard(
        title: _type == EntryType.sale ? 'Received via' : 'Paid via',
        icon: Icons.account_balance_wallet_outlined,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final m in options.paymentModes)
                ChoiceChip(
                  label: Text(m.label),
                  selected: m.value == _paymentMode,
                  showCheckmark: false,
                  selectedColor: accent,
                  labelStyle: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: m.value == _paymentMode
                        ? Colors.white
                        : AppColors.textPrimary,
                  ),
                  onSelected: (_) => setState(() => _paymentMode = m.value),
                ),
            ],
          ),
          if (_errors['payment_mode'] != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                _errors['payment_mode']!,
                style: const TextStyle(color: AppColors.danger, fontSize: 13),
              ),
            ),
          if (_paymentMode == PaymentModeOption.bank &&
              options.bankAccounts.isNotEmpty) ...[
            const SizedBox(height: 12),
            PickerField(
              label: 'Bank account',
              icon: Icons.account_balance_outlined,
              accent: accent,
              clearable: true,
              options: {for (final b in options.bankAccounts) b.id: b.name},
              selected: _bankId,
              placeholder: 'Choose an account',
              error: _errors['bank_account'],
              onChanged: (id) => setState(() => _bankId = id),
            ),
          ],
        ],
      ),
      FormCard(
        title: 'Note',
        icon: Icons.notes_outlined,
        children: [
          TextField(
            controller: _note,
            minLines: 2,
            maxLines: 3,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              hintText: 'Add a note (optional)',
            ),
          ),
        ],
      ),
      const SizedBox(height: 20),
      AppButton(
        label: 'Save ${_type.label.toLowerCase()}',
        color: accent,
        loading: _saving,
        onPressed: _categoryId != null && _amount.text.trim().isNotEmpty
            ? _save
            : null,
      ),
    ];
  }
}
