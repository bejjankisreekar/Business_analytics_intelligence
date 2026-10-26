import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Receivables and payables grouped by how overdue they are.
class AgingScreen extends ConsumerStatefulWidget {
  const AgingScreen({super.key});

  @override
  ConsumerState<AgingScreen> createState() => _AgingScreenState();
}

class _AgingScreenState extends ConsumerState<AgingScreen> {
  bool _receivables = true;

  @override
  Widget build(BuildContext context) {
    return ReportPage(
      title: 'Receivables & payables',
      onRefresh: () => ref.refresh(agingProvider.future),
      tabs: TabPills<bool>(
        selected: _receivables,
        onChanged: (v) => setState(() => _receivables = v),
        items: const [
          TabPillItem(
            value: true,
            label: 'Receivables',
            icon: Icons.south_west,
            color: AppColors.sale,
          ),
          TabPillItem(
            value: false,
            label: 'Payables',
            icon: Icons.north_east,
            color: AppColors.expense,
          ),
        ],
      ),
      children: [
        asyncBody(
          ref.watch(agingProvider),
          onRetry: () => ref.invalidate(agingProvider),
          builder: (data) {
            final buckets = (data['buckets'] as List).cast<Json>();
            final report =
                data[_receivables ? 'receivables' : 'payables'] as Json;
            final totals = report['totals'] as Json;
            final rows = (report['rows'] as List).cast<Json>();
            final color = _receivables ? AppColors.sale : AppColors.expense;
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                StatGrid(
                  tiles: [
                    StatTile(
                      label: _receivables ? 'To receive' : 'To pay',
                      value: rupees(parseNum(totals['total'])),
                      color: color,
                    ),
                    for (final b in buckets)
                      StatTile(
                        label: '${b['label']}',
                        value: rupees(parseNum(totals[b['key']])),
                      ),
                  ],
                ),
                SectionTitle(_receivables ? 'By customer' : 'By vendor'),
                if (rows.isEmpty)
                  const ReportCard(child: Text('Nothing outstanding.')),
                for (final row in rows)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: ReportCard(
                      child: Column(
                        children: [
                          LineRow(
                            '${row['name']}',
                            rupees(parseNum(row['total'])),
                            bold: true,
                            color: color,
                          ),
                          for (final b in buckets)
                            if (parseNum(row[b['key']]) != 0)
                              LineRow(
                                '${b['label']}',
                                rupees(parseNum(row[b['key']])),
                              ),
                        ],
                      ),
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
