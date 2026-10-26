import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Where a category's quantity came from: by sub-category, by item under it, and by day.
class QuantityDetailScreen extends ConsumerWidget {
  const QuantityDetailScreen({
    super.key,
    required this.kind,
    required this.name,
    required this.from,
    required this.to,
  });

  /// `sale` or `purchase`.
  final String kind;
  final String name;

  /// ISO dates, inclusive.
  final String from;
  final String to;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final key = (kind, name, from, to);
    final range = from == to
        ? displayDate(DateTime.parse(from))
        : '${displayDate(DateTime.parse(from))} – ${displayDate(DateTime.parse(to))}';
    final color = kind == 'purchase' ? AppColors.purchase : AppColors.sale;

    return ReportPage(
      title: name,
      onRefresh: () => ref.refresh(categoryDetailProvider(key).future),
      children: [
        asyncBody(
          ref.watch(categoryDetailProvider(key)),
          onRetry: () => ref.invalidate(categoryDetailProvider(key)),
          builder: (data) {
            final entries = (data['entries'] as List).cast<Json>();
            final withQty = entries
                .where((e) => parseNum(e['quantity']) > 0)
                .toList();
            final totalQty = withQty.fold<num>(
              0,
              (a, e) => a + parseNum(e['quantity']),
            );
            final totalAmount = entries.fold<num>(
              0,
              (a, e) => a + parseNum(e['amount']),
            );
            final groups = _group(withQty);
            final days = _byDay(withQty);
            final noQty = entries.length - withQty.length;

            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                HeroCard(
                  eyebrow: 'Quantity · $range',
                  value: '${totalQty.round()} units',
                  caption: totalQty > 0
                      ? '${rupees(totalAmount)} revenue · ${rupees(totalAmount / totalQty)} per unit'
                      : null,
                  figures: [
                    (
                      label: 'Entries',
                      value: '${entries.length}',
                      icon: Icons.list_alt_outlined,
                    ),
                    (
                      label: 'With qty',
                      value: '${withQty.length}',
                      icon: Icons.inventory_2_outlined,
                    ),
                    (
                      label: 'Sub-categories',
                      value: '${groups.length}',
                      icon: Icons.account_tree_outlined,
                    ),
                  ],
                ),
                if (totalQty == 0)
                  const Padding(
                    padding: EdgeInsets.only(top: 16),
                    child: ReportCard(
                      child: Text(
                        'No quantity was recorded on these entries.',
                        style: TextStyle(color: AppColors.textSecondary),
                      ),
                    ),
                  )
                else ...[
                  const SectionTitle('By sub-category'),
                  for (final g in groups)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: _GroupCard(
                        group: g,
                        totalQty: totalQty,
                        color: color,
                      ),
                    ),
                  if (noQty > 0)
                    Padding(
                      padding: const EdgeInsets.only(top: 2, bottom: 6),
                      child: Text(
                        '$noQty ${noQty == 1 ? 'entry has' : 'entries have'} no quantity recorded and ${noQty == 1 ? 'is' : 'are'} not counted here.',
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ),
                  SectionTitle(
                    'By day',
                    trailing: Text(
                      '${days.length} ${days.length == 1 ? 'day' : 'days'}',
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ),
                  BreakdownBars(
                    items: [
                      for (final d in days)
                        {
                          'name': displayDate(DateTime.parse(d.key)),
                          'quantity': d.value,
                          'date': d.key,
                        },
                    ],
                    color: color,
                    limit: 62,
                    quantityMode: true,
                    onItemTap: (label) {
                      final day = days.firstWhere(
                        (d) => displayDate(DateTime.parse(d.key)) == label,
                      );
                      context.push(dayLocation(day.key));
                    },
                  ),
                ],
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  onPressed: () => context.push(
                    categoryLocation(
                      kind: kind,
                      name: name,
                      from: from,
                      to: to,
                    ),
                  ),
                  icon: const Icon(Icons.list_alt_outlined),
                  label: const Text('See all entries'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                    foregroundColor: AppColors.textPrimary,
                    backgroundColor: AppColors.card,
                    side: const BorderSide(color: AppColors.divider),
                  ),
                ),
              ],
            );
          },
        ),
      ],
    );
  }
}

class _Group {
  _Group(this.name);

  final String name;
  num qty = 0;
  num amount = 0;
  int entries = 0;

  /// Item name -> [qty, amount].
  final Map<String, List<num>> items = {};
}

const _unspecified = 'Not specified';

/// Groups entries by sub-category, with the items under each. The API gives `Sub › Item`.
List<_Group> _group(List<Json> entries) {
  final groups = <String, _Group>{};
  for (final e in entries) {
    final label = '${e['subcategory']}'.trim();
    final parts = label.isEmpty
        ? <String>[]
        : label.split('›').map((p) => p.trim()).toList();
    final sub = parts.isEmpty ? _unspecified : parts.first;
    final item = parts.length > 1 ? parts.sublist(1).join(' › ') : null;
    final g = groups.putIfAbsent(sub, () => _Group(sub));
    final qty = parseNum(e['quantity']);
    final amount = parseNum(e['amount']);
    g.qty += qty;
    g.amount += amount;
    g.entries++;
    final row = g.items.putIfAbsent(item ?? '$sub (direct)', () => [0, 0]);
    row[0] += qty;
    row[1] += amount;
  }
  final list = groups.values.toList()..sort((a, b) => b.qty.compareTo(a.qty));
  return list;
}

/// (ISO date, quantity) newest first.
List<MapEntry<String, num>> _byDay(List<Json> entries) {
  final days = <String, num>{};
  for (final e in entries) {
    final d = '${e['date']}';
    days[d] = (days[d] ?? 0) + parseNum(e['quantity']);
  }
  return days.entries.toList()..sort((a, b) => b.key.compareTo(a.key));
}

class _GroupCard extends StatefulWidget {
  const _GroupCard({
    required this.group,
    required this.totalQty,
    required this.color,
  });

  final _Group group;
  final num totalQty;
  final Color color;

  @override
  State<_GroupCard> createState() => _GroupCardState();
}

class _GroupCardState extends State<_GroupCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final g = widget.group;
    final share = widget.totalQty > 0 ? g.qty / widget.totalQty : 0.0;
    final items = g.items.entries.toList()
      ..sort((a, b) => b.value[0].compareTo(a.value[0]));
    final expandable =
        items.length > 1 ||
        (items.length == 1 && !items.first.key.endsWith('(direct)'));
    return ReportCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: expandable ? () => setState(() => _open = !_open) : null,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          g.name,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      Text(
                        '${g.qty.round()} qty',
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      if (expandable) ...[
                        const SizedBox(width: 6),
                        Icon(
                          _open
                              ? Icons.keyboard_arrow_up
                              : Icons.keyboard_arrow_down,
                          color: AppColors.textSecondary,
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${rupees(g.amount)} · ${rupees(g.amount / g.qty)} per unit · ${g.entries} ${g.entries == 1 ? 'entry' : 'entries'}',
                          style: const TextStyle(
                            fontSize: 12,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ),
                      Text(
                        '${(share * 100).round()}%',
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: LinearProgressIndicator(
                      value: share.clamp(0, 1).toDouble(),
                      minHeight: 8,
                      color: widget.color,
                      backgroundColor: widget.color.withValues(alpha: 0.12),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (_open) ...[
            const Divider(height: 1, color: AppColors.divider),
            for (final i in items)
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        i.key,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 13),
                      ),
                    ),
                    Text(
                      '${i.value[0].round()} qty',
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    SizedBox(
                      width: 84,
                      child: Text(
                        rupeesShort(i.value[1]),
                        textAlign: TextAlign.right,
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 4),
          ],
        ],
      ),
    );
  }
}
