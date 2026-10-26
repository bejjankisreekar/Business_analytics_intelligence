import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Supplier directory with the amount still owed to each (read-only).
class VendorsScreen extends ConsumerWidget {
  const VendorsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('Vendors')),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(vendorsProvider.future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              ref.watch(vendorsProvider),
              onRetry: () => ref.invalidate(vendorsProvider),
              builder: (data) {
                final vendors = (data['vendors'] as List).cast<Json>();
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    StatGrid(
                      tiles: [
                        StatTile(
                          label: 'Total owed to vendors',
                          value: rupees(parseNum(data['total_outstanding'])),
                          color: AppColors.expense,
                        ),
                      ],
                    ),
                    const SectionTitle('Directory'),
                    if (vendors.isEmpty)
                      const ReportCard(
                        child: Text('No vendors yet. Add them in the web app.'),
                      ),
                    for (final v in vendors)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: ReportCard(
                          onTap: () => context.push(
                            ledgerLocation(
                              kind: 'vendor',
                              id: '${v['id']}',
                              title: '${v['name']}',
                            ),
                          ),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      '${v['name']}',
                                      style: const TextStyle(
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                                    Text(
                                      [
                                        if ('${v['phone']}'.isNotEmpty)
                                          '${v['phone']}',
                                        if ('${v['details']}'.isNotEmpty)
                                          '${v['details']}',
                                        if (v['is_active'] != true) 'inactive',
                                      ].join(' · '),
                                      style: const TextStyle(
                                        fontSize: 12,
                                        color: AppColors.textSecondary,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              Column(
                                crossAxisAlignment: CrossAxisAlignment.end,
                                children: [
                                  Text(
                                    rupees(parseNum(v['outstanding'])),
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w800,
                                    ),
                                  ),
                                  const Text(
                                    'owed',
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: AppColors.textSecondary,
                                    ),
                                  ),
                                ],
                              ),
                              const TapChevron(),
                            ],
                          ),
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
