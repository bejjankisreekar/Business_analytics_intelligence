import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/entry_line_tile.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../entries/models/entry_type.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Everything recorded on one day, with opening/closing cash and bank. Step through days with the arrows.
class DayDetailScreen extends ConsumerStatefulWidget {
  const DayDetailScreen({super.key, required this.date});

  /// ISO date to open on.
  final String date;

  @override
  ConsumerState<DayDetailScreen> createState() => _DayDetailScreenState();
}

class _DayDetailScreenState extends ConsumerState<DayDetailScreen> {
  late DateTime _date = DateTime.tryParse(widget.date) ?? DateTime.now();

  bool get _isToday {
    final now = DateTime.now();
    return _date.year == now.year &&
        _date.month == now.month &&
        _date.day == now.day;
  }

  void _shift(int days) =>
      setState(() => _date = _date.add(Duration(days: days)));

  Future<void> _pick() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
    );
    if (picked != null) setState(() => _date = picked);
  }

  @override
  Widget build(BuildContext context) {
    final date = isoDate(_date);
    final detail = ref.watch(dayDetailProvider(date));

    return Scaffold(
      appBar: AppBar(title: const Text('Day details')),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(dayDetailProvider(date).future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(
              children: [
                IconButton.outlined(
                  onPressed: () => _shift(-1),
                  icon: const Icon(Icons.chevron_left),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _pick,
                    icon: const Icon(Icons.calendar_today, size: 18),
                    label: Text(displayDate(_date)),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(48),
                      backgroundColor: AppColors.card,
                      foregroundColor: AppColors.textPrimary,
                      side: const BorderSide(color: AppColors.divider),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  onPressed: _isToday ? null : () => _shift(1),
                  icon: const Icon(Icons.chevron_right),
                ),
              ],
            ),
            asyncBody(
              detail,
              onRetry: () => ref.invalidate(dayDetailProvider(date)),
              builder: (data) => _Content(data: data, date: date),
            ),
          ],
        ),
      ),
    );
  }
}

class _Content extends StatefulWidget {
  const _Content({required this.data, required this.date});

  final Json data;
  final String date;

  @override
  State<_Content> createState() => _ContentState();
}

class _ContentState extends State<_Content> {
  Json get data => widget.data;
  String get date => widget.date;

  /// Opening/closing balances are optional, so they start collapsed.
  bool _showBalances = false;

  @override
  Widget build(BuildContext context) {
    final totals = data['totals'] as Json;
    final entries = data['entries'] as Json;
    final net = parseNum(totals['net']);
    final isEmpty = EntryType.values.every(
      (t) => ((entries[t.apiValue] as List?) ?? const []).isEmpty,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 16),
        HeroCard(
          eyebrow: 'Net for the day',
          value: rupees(net),
          caption:
              'Closing balance ${rupees(parseNum(data['closing_cash']) + parseNum(data['closing_bank']))}',
          figures: [
            (
              label: 'Revenue',
              value: rupeesShort(parseNum(totals['sale'])),
              icon: Icons.south_west,
            ),
            (
              label: 'Expense',
              value: rupeesShort(parseNum(totals['expense'])),
              icon: Icons.north_east,
            ),
            (
              label: 'Purchase',
              value: rupeesShort(parseNum(totals['purchase'])),
              icon: Icons.shopping_bag_outlined,
            ),
          ],
        ),
        SectionTitle(
          'Cash and bank',
          trailing: IconButton(
            tooltip: _showBalances ? 'Hide' : 'Show',
            visualDensity: VisualDensity.compact,
            onPressed: () => setState(() => _showBalances = !_showBalances),
            icon: Icon(
              _showBalances
                  ? Icons.keyboard_arrow_up
                  : Icons.keyboard_arrow_down,
            ),
          ),
        ),
        if (_showBalances)
          StatGrid(
            tiles: [
              StatTile(
                label: 'Opening cash',
                value: rupees(parseNum(data['opening_cash'])),
                icon: Icons.payments_outlined,
              ),
              StatTile(
                label: 'Closing cash',
                value: rupees(parseNum(data['closing_cash'])),
                icon: Icons.payments_outlined,
                color: AppColors.purchase,
              ),
              StatTile(
                label: 'Opening bank',
                value: rupees(parseNum(data['opening_bank'])),
                icon: Icons.account_balance_outlined,
              ),
              StatTile(
                label: 'Closing bank',
                value: rupees(parseNum(data['closing_bank'])),
                icon: Icons.account_balance_outlined,
                color: AppColors.brand,
              ),
            ],
          ),
        if (isEmpty)
          const Padding(
            padding: EdgeInsets.only(top: 24),
            child: ReportCard(
              child: Text(
                'Nothing was recorded on this day.',
                style: TextStyle(color: AppColors.textSecondary),
              ),
            ),
          ),
        for (final type in EntryType.values)
          if (((entries[type.apiValue] as List?) ?? const []).isNotEmpty) ...[
            SectionTitle(
              '${type.label} · ${rupees(parseNum(totals[type.apiValue]))}',
              trailing: Text(
                '${(entries[type.apiValue] as List).length} entries',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            ReportCard(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Column(
                children: [
                  for (final e in (entries[type.apiValue] as List).cast<Json>())
                    EntryLineTile(
                      type: type,
                      title: '${e['category']}',
                      details: [
                        '${e['subcategory']}',
                        if (parseNum(e['quantity']) > 0)
                          'Qty ${parseNum(e['quantity']).round()}',
                        '${e['party']}',
                        '${e['payment_mode']}',
                        '${e['note']}',
                      ],
                      amount: parseNum(e['amount']),
                      onTap: () => context.push(
                        categoryLocation(
                          kind: type.apiValue,
                          name: '${e['category']}',
                          from: date,
                          to: date,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
      ],
    );
  }
}
