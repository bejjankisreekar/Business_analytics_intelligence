import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/entry_line_tile.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../entries/models/entry_options.dart';
import '../../entries/application/entries_providers.dart';
import '../../entries/models/entry_type.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

const _ranges = {
  'today': 'Today',
  'this_week': 'This week',
  'this_month': 'This month',
  'last_month': 'Last month',
  'this_quarter': 'This quarter',
  'year_to_date': 'Year to date',
  'custom': 'Custom range',
};

/// Statement for one category (optionally one sub-category) over a date range, like the web report.
class CategoryStatementScreen extends ConsumerStatefulWidget {
  const CategoryStatementScreen({super.key});

  @override
  ConsumerState<CategoryStatementScreen> createState() =>
      _CategoryStatementScreenState();
}

class _CategoryStatementScreenState
    extends ConsumerState<CategoryStatementScreen> {
  EntryType _type = EntryType.sale;
  String? _categoryId;
  String _subcategoryId = '';
  String _range = 'this_month';
  DateTimeRange? _custom;

  DateTimeRange get _dates {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    return switch (_range) {
      'today' => DateTimeRange(start: today, end: today),
      'this_week' => DateTimeRange(
        start: today.subtract(Duration(days: today.weekday - 1)),
        end: today,
      ),
      'last_month' => DateTimeRange(
        start: DateTime(today.year, today.month - 1, 1),
        end: DateTime(today.year, today.month, 0),
      ),
      'this_quarter' => DateTimeRange(
        start: DateTime(today.year, ((today.month - 1) ~/ 3) * 3 + 1, 1),
        end: today,
      ),
      'year_to_date' => DateTimeRange(
        start: DateTime(today.year, 1, 1),
        end: today,
      ),
      'custom' =>
        _custom ??
            DateTimeRange(
              start: DateTime(today.year, today.month, 1),
              end: today,
            ),
      _ => DateTimeRange(
        start: DateTime(today.year, today.month, 1),
        end: today,
      ),
    };
  }

  Future<void> _setRange(String? value) async {
    if (value == null) return;
    if (value == 'custom') {
      final now = DateTime.now();
      final picked = await showDateRangePicker(
        context: context,
        firstDate: DateTime(2000),
        lastDate: now,
        initialDateRange: _custom ?? _dates,
      );
      if (picked == null) return;
      setState(() {
        _custom = picked;
        _range = 'custom';
      });
      return;
    }
    setState(() => _range = value);
  }

  @override
  Widget build(BuildContext context) {
    final options = ref.watch(entryOptionsProvider);
    final categories =
        options.value?.categories[_type] ?? const <EntryCategory>[];
    final category = categories.where((c) => c.id == _categoryId).firstOrNull;

    return ReportPage(
      title: 'Category statement',
      onRefresh: () async => ref.invalidate(entryOptionsProvider),
      tabs: TabPills<EntryType>(
        selected: _type,
        onChanged: (v) => setState(() {
          _type = v;
          _categoryId = null;
          _subcategoryId = '';
        }),
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
              label: 'Category',
              icon: Icons.category_outlined,
              value: _categoryId,
              items: {for (final c in categories) c.id: c.name},
              onChanged: (v) => setState(() {
                _categoryId = v;
                _subcategoryId = '';
              }),
            ),
          ],
        ),
        if (category != null && category.subcategories.isNotEmpty)
          FilterRow(
            children: [
              DropdownField(
                label: 'Sub-category',
                icon: Icons.account_tree_outlined,
                value: _subcategoryId,
                items: {
                  '': 'All sub-categories',
                  for (final s in category.subcategories) s.id: s.name,
                },
                onChanged: (v) => setState(() => _subcategoryId = v ?? ''),
              ),
            ],
          ),
        FilterRow(
          children: [
            DropdownField(
              label: 'Date range',
              icon: Icons.date_range_outlined,
              value: _range,
              items: _ranges,
              onChanged: _setRange,
            ),
          ],
        ),
      ],
      children: [
        asyncBody(
          options,
          onRetry: () => ref.invalidate(entryOptionsProvider),
          builder: (_) => category == null
              ? const ReportCard(
                  child: Text(
                    'Pick a category to see every entry behind its total.',
                    style: TextStyle(color: AppColors.textSecondary),
                  ),
                )
              : _Statement(
                  type: _type,
                  categoryName: category.name,
                  subcategoryId: _subcategoryId,
                  range: _dates,
                ),
        ),
      ],
    );
  }
}

class _Statement extends ConsumerWidget {
  const _Statement({
    required this.type,
    required this.categoryName,
    required this.subcategoryId,
    required this.range,
  });

  final EntryType type;
  final String categoryName;
  final String subcategoryId;
  final DateTimeRange range;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final key = (
      type.apiValue,
      categoryName,
      subcategoryId,
      isoDate(range.start),
      isoDate(range.end),
    );
    final label = range.start == range.end
        ? displayDate(range.start)
        : '${displayDate(range.start)} – ${displayDate(range.end)}';
    return asyncBody(
      ref.watch(categoryStatementProvider(key)),
      onRetry: () => ref.invalidate(categoryStatementProvider(key)),
      builder: (data) {
        final entries = (data['entries'] as List).cast<Json>();
        final qty = parseNum(data['total_quantity']);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            HeroCard(
              eyebrow: '$categoryName · $label',
              value: rupees(parseNum(data['total'])),
              caption:
                  '${data['count']} ${data['count'] == 1 ? 'entry' : 'entries'}'
                  '${qty > 0 ? ' · Qty ${qty.round()}' : ''}',
              figures: const [],
            ),
            const SectionTitle('Entries'),
            if (entries.isEmpty)
              const ReportCard(
                child: Text(
                  'No entries in this range.',
                  style: TextStyle(color: AppColors.textSecondary),
                ),
              )
            else
              ReportCard(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  children: [
                    for (final e in entries)
                      EntryLineTile(
                        type: type,
                        title: displayDate(DateTime.parse(e['date'] as String)),
                        details: [
                          '${e['subcategory']}',
                          if (parseNum(e['quantity']) > 0)
                            'Qty ${parseNum(e['quantity']).round()}',
                          '${e['party']}',
                          '${e['payment_mode']}',
                          '${e['note']}',
                        ],
                        amount: parseNum(e['amount']),
                        onTap: () =>
                            context.push(dayLocation(e['date'] as String)),
                      ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}
