import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Each bank account with its current balance (read-only; managed on the web).
class BankAccountsScreen extends ConsumerWidget {
  const BankAccountsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('Bank accounts')),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(bankAccountsProvider.future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              ref.watch(bankAccountsProvider),
              onRetry: () => ref.invalidate(bankAccountsProvider),
              builder: (data) {
                final accounts = (data['accounts'] as List).cast<Json>();
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    StatGrid(
                      tiles: [
                        StatTile(
                          label: 'Total in active accounts',
                          value: rupees(parseNum(data['total'])),
                        ),
                      ],
                    ),
                    const SectionTitle('Accounts'),
                    if (accounts.isEmpty)
                      const ReportCard(
                        child: Text(
                          'No bank accounts yet. Add them in the web app.',
                        ),
                      ),
                    for (final a in accounts)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: ReportCard(
                          onTap: () => context.push(
                            ledgerLocation(
                              kind: 'bank',
                              id: '${a['id']}',
                              title: '${a['name']}',
                            ),
                          ),
                          child: Row(
                            children: [
                              const IconBadge(
                                icon: Icons.account_balance_outlined,
                                color: AppColors.brand,
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      '${a['name']}',
                                      style: const TextStyle(
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                                    Text(
                                      [
                                        '${a['bank']}',
                                        if ('${a['account_number']}'.isNotEmpty)
                                          '${a['account_number']}',
                                        if (a['is_active'] != true) 'inactive',
                                      ].join(' · '),
                                      style: const TextStyle(
                                        fontSize: 12,
                                        color: AppColors.textSecondary,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              Text(
                                rupees(parseNum(a['balance'])),
                                style: const TextStyle(
                                  fontWeight: FontWeight.w800,
                                ),
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
