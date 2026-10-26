import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Day-by-day revenue, expense, purchase and net over a date range.
class DailyPerformanceScreen extends ConsumerStatefulWidget {
  const DailyPerformanceScreen({super.key, this.initialFrom, this.initialTo});

  /// ISO dates; defaults to this month so far.
  final String? initialFrom;
  final String? initialTo;

  @override
  ConsumerState<DailyPerformanceScreen> createState() =>
      _DailyPerformanceScreenState();
}

class _DailyPerformanceScreenState
    extends ConsumerState<DailyPerformanceScreen> {
  late DateTimeRange _range = () {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final from = DateTime.tryParse(widget.initialFrom ?? '');
    final to = DateTime.tryParse(widget.initialTo ?? '');
    if (from != null && to != null && !to.isBefore(from))
      return DateTimeRange(start: from, end: to);
    return DateTimeRange(
      start: DateTime(today.year, today.month, 1),
      end: today,
    );
  }();

  Future<void> _pick() async {
    final picked = await showDateRangePicker(
      context: context,
      initialDateRange: _range,
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
    );
    if (picked != null) setState(() => _range = picked);
  }

  @override
  Widget build(BuildContext context) {
    final key = (isoDate(_range.start), isoDate(_range.end));
    final result = ref.watch(dailyPerformanceProvider(key));

    return ReportPage(
      title: 'Daily performance',
      onRefresh: () => ref.refresh(dailyPerformanceProvider(key).future),
      filters: [
        FilterRow(
          children: [
            DateRangeField(
              label: 'Date range',
              text: '${displayDate(_range.start)} – ${displayDate(_range.end)}',
              onTap: _pick,
            ),
          ],
        ),
      ],
      children: [
        asyncBody(
          result,
          onRetry: () => ref.invalidate(dailyPerformanceProvider(key)),
          builder: (data) {
            final days = (data['days'] as List).cast<Json>();
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionTitle('Total for the range'),
                TotalsRow(
                  title: '${days.length} days',
                  data: data['totals'] as Json,
                ),
                const SectionTitle('By day'),
                for (final day in days)
                  TotalsRow(
                    title: displayDate(DateTime.parse(day['date'] as String)),
                    data: day,
                    onTap: () =>
                        context.push(dayLocation(day['date'] as String)),
                  ),
              ],
            );
          },
        ),
      ],
    );
  }
}
