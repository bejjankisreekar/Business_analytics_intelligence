import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/entry_line_tile.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../entries/models/entry_type.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Every category with its total and a per-period breakdown, like the web Monthly Summary.
class MonthlySummaryScreen extends ConsumerStatefulWidget {
  const MonthlySummaryScreen({super.key});

  @override
  ConsumerState<MonthlySummaryScreen> createState() =>
      _MonthlySummaryScreenState();
}

class _MonthlySummaryScreenState extends ConsumerState<MonthlySummaryScreen> {
  EntryType _type = EntryType.sale;
  String _granularity = 'monthly';
  String _period = 'this_fy';

  @override
  Widget build(BuildContext context) {
    final key = (_type.apiValue, _granularity, _period);
    return ReportPage(
      title: 'Monthly summary',
      onRefresh: () => ref.refresh(monthlySummaryProvider(key).future),
      tabs: TabPills<EntryType>(
        selected: _type,
        onChanged: (v) => setState(() => _type = v),
        items: [
          for (final t in EntryType.values)
            TabPillItem(
              value: t,
              label: t.label,
              icon: entryIcon(t),
              color: t.color,
            ),
        ],
      ),
      filters: [
        FilterRow(
          children: [
            DropdownField(
              label: 'View by',
              icon: Icons.view_timeline_outlined,
              value: _granularity,
              items: kGranularities,
              onChanged: (v) =>
                  setState(() => _granularity = v ?? _granularity),
            ),
            PeriodDropdown(
              value: _period,
              onChanged: (v) => setState(() => _period = v),
            ),
          ],
        ),
      ],
      children: [
        asyncBody(
          ref.watch(monthlySummaryProvider(key)),
          onRetry: () => ref.invalidate(monthlySummaryProvider(key)),
          builder: (data) =>
              _Summary(data: data, type: _type, granularity: _granularity),
        ),
      ],
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({
    required this.data,
    required this.type,
    required this.granularity,
  });

  final Json data;
  final EntryType type;
  final String granularity;

  @override
  Widget build(BuildContext context) {
    final period = data['period'] as Json;
    final yoy = data['yoy'] as Json;
    final rows = (data['rows'] as List).cast<Json>();
    final labels = (data['labels'] as List).cast<String>();
    final grand = parseNum(data['grand_total']);
    final from = '${period['from']}';
    final to = '${period['to']}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _YearOverYear(label: '${period['label']}', yoy: yoy, color: type.color),
        SectionTitle(
          '${type.label} by category',
          trailing: Text(
            '${rows.length} ${rows.length == 1 ? 'category' : 'categories'}',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
        ),
        if (rows.isEmpty)
          ReportCard(
            child: Text(
              'No ${type.label.toLowerCase()} recorded for ${period['label']}.',
              style: const TextStyle(color: AppColors.textSecondary),
            ),
          )
        else ...[
          for (final row in rows)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _CategoryCard(
                row: row,
                labels: labels,
                share: grand <= 0 ? 0 : parseNum(row['total']) / grand,
                color: type.color,
                onViewEntries: () => context.push(
                  categoryLocation(
                    kind: type.apiValue,
                    name: '${row['name']}',
                    from: from,
                    to: to,
                  ),
                ),
              ),
            ),
          if (data['truncated'] == true)
            const Padding(
              padding: EdgeInsets.only(bottom: 10),
              child: Text(
                'Showing the first periods only. Pick a shorter range or a coarser view to see the rest.',
                style: TextStyle(fontSize: 12, color: AppColors.expense),
              ),
            ),
          _TotalCard(
            total: grand,
            labels: labels,
            cells: (data['column_totals'] as List).cast<Json>(),
            color: type.color,
          ),
        ],
        const Padding(
          padding: EdgeInsets.only(top: 12),
          child: Text(
            'Δ% is the change from the previous period in the same row.',
            style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
          ),
        ),
      ],
    );
  }
}

class _YearOverYear extends StatelessWidget {
  const _YearOverYear({
    required this.label,
    required this.yoy,
    required this.color,
  });

  final String label;
  final Json yoy;
  final Color color;

  @override
  Widget build(BuildContext context) => ReportCard(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label.toUpperCase(),
                style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.4,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            DeltaPill(pct: yoy['change_pct']),
          ],
        ),
        const SizedBox(height: 6),
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Text(
            rupees(parseNum(yoy['current_total'])),
            style: TextStyle(
              fontSize: 28,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
        ),
        const Divider(height: 24, color: AppColors.divider),
        Row(
          children: [
            const Icon(Icons.history, size: 16, color: AppColors.textSecondary),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                'Same range last year',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
            ),
            Text(
              rupees(parseNum(yoy['prior_total'])),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ],
        ),
      ],
    ),
  );
}

class _CategoryCard extends StatefulWidget {
  const _CategoryCard({
    required this.row,
    required this.labels,
    required this.share,
    required this.color,
    required this.onViewEntries,
  });

  final Json row;
  final List<String> labels;
  final double share;
  final Color color;
  final VoidCallback onViewEntries;

  @override
  State<_CategoryCard> createState() => _CategoryCardState();
}

class _CategoryCardState extends State<_CategoryCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final row = widget.row;
    final subrows = (row['subrows'] as List).cast<Json>();
    final cells = (row['cells'] as List).cast<Json>();
    return ReportCard(
      padding: EdgeInsets.zero,
      child: Column(
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${row['name']}',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      Text(
                        rupees(parseNum(row['total'])),
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Icon(
                        _open
                            ? Icons.keyboard_arrow_up
                            : Icons.keyboard_arrow_down,
                        color: AppColors.textSecondary,
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: LinearProgressIndicator(
                            value: widget.share.clamp(0, 1).toDouble(),
                            minHeight: 8,
                            color: widget.color,
                            backgroundColor: widget.color.withValues(
                              alpha: 0.12,
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(
                        '${(widget.share * 100).toStringAsFixed(1)}%',
                        style: const TextStyle(
                          fontSize: 12,
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          if (_open) ...[
            const Divider(height: 1, color: AppColors.divider),
            if (subrows.isNotEmpty) ...[
              const _Heading('Sub-categories'),
              for (final s in subrows)
                _AmountLine(
                  name: '${s['name']}',
                  value: rupees(parseNum(s['total'])),
                ),
            ],
            const _Heading('By period'),
            _PeriodLines(labels: widget.labels, cells: cells),
            Padding(
              padding: const EdgeInsets.fromLTRB(8, 0, 8, 6),
              child: Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: widget.onViewEntries,
                  icon: const Icon(Icons.list_alt_outlined, size: 18),
                  label: const Text('View entries'),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
    child: Align(
      alignment: Alignment.centerLeft,
      child: Text(
        text.toUpperCase(),
        style: const TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.4,
          color: AppColors.textSecondary,
        ),
      ),
    ),
  );
}

class _AmountLine extends StatelessWidget {
  const _AmountLine({required this.name, required this.value, this.change});

  final String name;
  final String value;
  final Object? change;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
    child: Row(
      children: [
        Expanded(
          child: Text(
            name,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 13),
          ),
        ),
        Text(
          value,
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
        ),
        SizedBox(
          width: 74,
          child: Align(
            alignment: Alignment.centerRight,
            child: _Change(change),
          ),
        ),
      ],
    ),
  );
}

class _Change extends StatelessWidget {
  const _Change(this.pct);

  final Object? pct;

  @override
  Widget build(BuildContext context) {
    if (pct == null) return const SizedBox.shrink();
    final v = parseNum(pct);
    final up = v >= 0;
    return Text(
      '${up ? '▲' : '▼'} ${v.abs()}%',
      style: TextStyle(
        fontSize: 11,
        fontWeight: FontWeight.w700,
        color: up ? AppColors.sale : AppColors.expense,
      ),
    );
  }
}

/// One line per period that has activity, with the change from the period before it.
class _PeriodLines extends StatelessWidget {
  const _PeriodLines({required this.labels, required this.cells});

  final List<String> labels;
  final List<Json> cells;

  @override
  Widget build(BuildContext context) {
    final lines = <Widget>[];
    for (var i = 0; i < cells.length && i < labels.length; i++) {
      final value = parseNum(cells[i]['value']);
      if (value == 0) continue;
      lines.add(
        _AmountLine(
          name: labels[i],
          value: rupees(value),
          change: cells[i]['change'],
        ),
      );
    }
    if (lines.isEmpty) {
      return const Padding(
        padding: EdgeInsets.fromLTRB(16, 4, 16, 8),
        child: Text(
          'Nothing in these periods.',
          style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
        ),
      );
    }
    return Column(children: lines);
  }
}

class _TotalCard extends StatefulWidget {
  const _TotalCard({
    required this.total,
    required this.labels,
    required this.cells,
    required this.color,
  });

  final num total;
  final List<String> labels;
  final List<Json> cells;
  final Color color;

  @override
  State<_TotalCard> createState() => _TotalCardState();
}

class _TotalCardState extends State<_TotalCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) => Container(
    decoration: BoxDecoration(
      gradient: AppColors.heroGradient,
      borderRadius: BorderRadius.circular(20),
    ),
    child: Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: Column(
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: () => setState(() => _open = !_open),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Total',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  Text(
                    rupees(widget.total),
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 18,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Icon(
                    _open ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                    color: Colors.white,
                  ),
                ],
              ),
            ),
          ),
          if (_open)
            Container(
              decoration: BoxDecoration(
                color: AppColors.card,
                borderRadius: BorderRadius.circular(20),
              ),
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: _PeriodLines(labels: widget.labels, cells: widget.cells),
            ),
        ],
      ),
    ),
  );
}
