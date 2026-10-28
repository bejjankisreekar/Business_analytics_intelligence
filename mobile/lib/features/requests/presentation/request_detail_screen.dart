import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme/app_colors.dart';
import '../../../core/network/api_exception.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/branded_refresh.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/data/reports_repository.dart' show Json;
import '../application/requests_providers.dart';

/// One customer request: set availability and a rate for each item, send the quote, then record the payment.
class RequestDetailScreen extends ConsumerWidget {
  const RequestDetailScreen({super.key, required this.number});

  final int number;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: Text('Request #$number')),
      body: asyncBody(
        ref.watch(requestDetailProvider(number)),
        onRetry: () => ref.invalidate(requestDetailProvider(number)),
        builder: (data) {
          final r = data['request'] as Json;
          // A new key rebuilds the form when the server's copy changes (after saving, or an edit by the customer).
          return _DetailBody(
            key: ValueKey(
              '${r['version']}|${r['status']}|${r['payment_status']}|${r['total']}',
            ),
            number: number,
            data: data,
          );
        },
      ),
    );
  }
}

class _ItemEdit {
  _ItemEdit(Json item)
    : id = item['id'] as int,
      name = item['product_name'] as String,
      qty = item['qty'] as String,
      unit = item['unit_label'] as String,
      available = item['available'] as bool,
      rate = TextEditingController(text: _trim(item['price'])),
      remark = TextEditingController(text: item['remark'] as String? ?? '');

  final int id;
  final String name;
  final String qty;
  final String unit;
  bool available;
  final TextEditingController rate;
  final TextEditingController remark;

  static String _trim(Object? price) {
    final text = price?.toString() ?? '';
    return text.endsWith('.00') ? text.substring(0, text.length - 3) : text;
  }

  num? get rateValue => num.tryParse(rate.text.trim());

  /// Rate x quantity rounded to paise, the way the server rounds each row.
  num get amount {
    final r = rateValue;
    if (!available || r == null) return 0;
    return (r * (num.tryParse(qty) ?? 0) * 100).round() / 100;
  }

  void dispose() {
    rate.dispose();
    remark.dispose();
  }
}

class _DetailBody extends ConsumerStatefulWidget {
  const _DetailBody({super.key, required this.number, required this.data});

  final int number;
  final Json data;

  @override
  ConsumerState<_DetailBody> createState() => _DetailBodyState();
}

class _DetailBodyState extends ConsumerState<_DetailBody> {
  late final List<_ItemEdit> _edits = [
    for (final i in (widget.data['items'] as List).cast<Json>()) _ItemEdit(i),
  ];
  String _payMode = 'CASH';
  bool _busy = false;

  Json get _req => widget.data['request'] as Json;

  bool get _quoted => _req['status'] == 'QUOTED';

  bool get _paid => _req['payment_status'] == 'PAID';

  @override
  void dispose() {
    for (final e in _edits) {
      e.dispose();
    }
    super.dispose();
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  void _refreshEverywhere() {
    ref.invalidate(requestDetailProvider(widget.number));
    ref.invalidate(requestsListProvider);
    ref.invalidate(waitingCountProvider);
  }

  /// Runs a save, then shows [done] or the server's reason it was refused.
  Future<void> _run(Future<void> Function() action, String done) async {
    setState(() => _busy = true);
    try {
      await action();
      _refreshEverywhere();
      _snack(done);
    } catch (error) {
      _snack(userMessage(error));
      // The customer changed their list while it was open here: show the new one.
      if (error is ApiException && error.code == 'stale_list') {
        _refreshEverywhere();
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveQuote({required bool send}) async {
    if (send) {
      for (final e in _edits) {
        if (e.available && e.rateValue == null) {
          _snack('Enter a rate for "${e.name}", or turn off Available.');
          return;
        }
      }
    }
    final items = <Json>[
      for (final e in _edits)
        {
          'id': e.id,
          'available': e.available,
          'price': e.available ? e.rate.text.trim() : '',
          'remark': e.remark.text.trim(),
        },
    ];
    await _run(
      () => ref
          .read(requestsRepositoryProvider)
          .saveQuote(
            widget.number,
            items: items,
            send: send,
            version: _req['version'] as String,
          ),
      send ? 'Quote sent. The customer can see it now.' : 'Draft saved.',
    );
  }

  Future<void> _setPayment({required bool paid}) => _run(
    () => ref
        .read(requestsRepositoryProvider)
        .setPayment(widget.number, paid: paid, mode: _payMode),
    paid
        ? 'Marked as paid and added to your Daily Book.'
        : 'Marked as payment pending.',
  );

  Future<void> _copy(String text, String what) async {
    await Clipboard.setData(ClipboardData(text: text));
    _snack('$what copied.');
  }

  String _when(String iso) {
    final t = DateTime.parse(iso).toLocal();
    final h = t.hour.toString().padLeft(2, '0');
    final m = t.minute.toString().padLeft(2, '0');
    return '${displayDate(t)}, $h:$m';
  }

  @override
  Widget build(BuildContext context) {
    final currency = widget.data['currency'] as String? ?? 'INR';
    final supplyCount = _edits.where((e) => e.available).length;
    final total = _edits.fold<num>(0, (sum, e) => sum + e.amount);
    final address = _req['customer_address'] as String? ?? '';

    return BrandedRefresh(
      onRefresh: () => ref.refresh(requestDetailProvider(widget.number).future),
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          ReportCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        _req['customer_name'] as String,
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                    _StatusPill(quoted: _quoted, paid: _paid),
                  ],
                ),
                const SizedBox(height: 6),
                SelectableText(
                  _req['customer_phone'] as String,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                if (address.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    address,
                    style: const TextStyle(color: AppColors.textSecondary),
                  ),
                ],
                const SizedBox(height: 6),
                Text(
                  'Received ${_when(_req['created_at'] as String)}',
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 12,
                  ),
                ),
                if (_req['edited_at'] != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    'The customer updated this list on ${_when(_req['edited_at'] as String)}. Check the items before quoting.',
                    style: const TextStyle(
                      color: AppColors.purchase,
                      fontWeight: FontWeight.w700,
                      fontSize: 12,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SectionTitle('Items'),
          for (final e in _edits)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _ItemCard(
                edit: e,
                currency: currency,
                onChanged: () => setState(() {}),
              ),
            ),
          ReportCard(
            child: Column(
              children: [
                LineRow('Requested', '${_edits.length} items'),
                LineRow('Can supply', '$supplyCount items'),
                LineRow(
                  'Quote total',
                  rupees(total),
                  bold: true,
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: _busy ? null : () => _saveQuote(send: false),
                  child: const Text('Save draft'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed: _busy ? null : () => _saveQuote(send: true),
                  child: Text(_quoted ? 'Update quote' : 'Send quote'),
                ),
              ),
            ],
          ),
          if (_quoted) ...[
            const SectionTitle('Payment'),
            ReportCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _paid
                        ? 'Paid ${_req['paid_at'] == null ? '' : 'on ${_when(_req['paid_at'] as String)} '}by ${_req['payment_mode'] == 'BANK' ? 'online' : 'cash'}. Ready to deliver. The amount is in your Daily Book.'
                        : 'Waiting for $currency ${_req['total']}. Mark it paid once the customer has paid.',
                  ),
                  const SizedBox(height: 12),
                  if (!_paid) ...[
                    SegmentedButton<String>(
                      segments: const [
                        ButtonSegment(value: 'CASH', label: Text('Cash')),
                        ButtonSegment(value: 'BANK', label: Text('Online')),
                      ],
                      selected: {_payMode},
                      onSelectionChanged: (s) =>
                          setState(() => _payMode = s.first),
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton(
                        onPressed: _busy ? null : () => _setPayment(paid: true),
                        child: const Text('Mark as paid'),
                      ),
                    ),
                  ] else
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton(
                        onPressed: _busy
                            ? null
                            : () => _setPayment(paid: false),
                        child: const Text('Mark as pending'),
                      ),
                    ),
                ],
              ),
            ),
          ],
          const SectionTitle('Share with the customer'),
          ReportCard(
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  icon: const Icon(Icons.link, size: 18),
                  label: const Text('Copy customer link'),
                  onPressed: () =>
                      _copy(_req['customer_link'] as String, 'Customer link'),
                ),
                if (_req['pdf_link'] case final String pdf)
                  OutlinedButton.icon(
                    icon: const Icon(Icons.picture_as_pdf_outlined, size: 18),
                    label: const Text('Copy PDF link'),
                    onPressed: () => _copy(pdf, 'PDF link'),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.quoted, required this.paid});

  final bool quoted;
  final bool paid;

  @override
  Widget build(BuildContext context) {
    final (text, color) = paid
        ? ('Paid', AppColors.sale)
        : quoted
        ? ('Quoted · unpaid', AppColors.brand)
        : ('Waiting', AppColors.purchase);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w800,
          fontSize: 12,
        ),
      ),
    );
  }
}

class _ItemCard extends StatelessWidget {
  const _ItemCard({
    required this.edit,
    required this.currency,
    required this.onChanged,
  });

  final _ItemEdit edit;
  final String currency;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final e = edit;
    final line = e.rateValue;
    return ReportCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  e.name,
                  style: TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 15,
                    decoration: e.available ? null : TextDecoration.lineThrough,
                    color: e.available
                        ? AppColors.textPrimary
                        : AppColors.textSecondary,
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 3,
                ),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(
                  '${e.qty} ${e.unit}',
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Switch(
                value: e.available,
                onChanged: (v) {
                  e.available = v;
                  onChanged();
                },
              ),
              const Text('Available'),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: e.rate,
                  enabled: e.available,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  inputFormatters: [
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                  ],
                  onChanged: (_) => onChanged(),
                  decoration: InputDecoration(
                    isDense: true,
                    labelText: 'Rate per ${e.unit}',
                    prefixText: '₹ ',
                  ),
                ),
              ),
            ],
          ),
          if (e.available && line != null) ...[
            const SizedBox(height: 6),
            Align(
              alignment: Alignment.centerRight,
              child: Text(
                '₹${formatAmount(line)} x ${e.qty} = ${rupees(e.amount)}',
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
            ),
          ],
          const SizedBox(height: 6),
          TextField(
            controller: e.remark,
            maxLength: 255,
            decoration: const InputDecoration(
              isDense: true,
              counterText: '',
              hintText: 'Remark (optional), e.g. not available today',
            ),
          ),
        ],
      ),
    );
  }
}
