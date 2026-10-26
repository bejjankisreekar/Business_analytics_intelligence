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

/// Every entry of one category over a date range, newest first.
class CategoryDetailScreen extends ConsumerWidget {
  const CategoryDetailScreen({
    super.key,
    required this.kind,
    required this.name,
    required this.from,
    required this.to,
  });

  /// `sale`, `expense` or `purchase`.
  final String kind;
  final String name;

  /// ISO dates, inclusive.
  final String from;
  final String to;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final type = EntryType.fromApi(kind);
    final key = (kind, name, from, to);
    final range = from == to
        ? displayDate(DateTime.parse(from))
        : '${displayDate(DateTime.parse(from))} – ${displayDate(DateTime.parse(to))}';

    return Scaffold(
      appBar: AppBar(title: Text(name)),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(categoryDetailProvider(key).future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              ref.watch(categoryDetailProvider(key)),
              onRetry: () => ref.invalidate(categoryDetailProvider(key)),
              builder: (data) {
                final entries = (data['entries'] as List).cast<Json>();
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    HeroCard(
                      eyebrow: '${type.label} · $range',
                      value: rupees(parseNum(data['total'])),
                      caption:
                          '${data['count']} ${data['count'] == 1 ? 'entry' : 'entries'}'
                          '${parseNum(data['total_quantity']) > 0 ? ' · Qty ${parseNum(data['total_quantity']).round()}' : ''}',
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
                                title: displayDate(
                                  DateTime.parse(e['date'] as String),
                                ),
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
                                  dayLocation(e['date'] as String),
                                ),
                              ),
                          ],
                        ),
                      ),
                  ],
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}
