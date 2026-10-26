import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

class CashPositionScreen extends ConsumerWidget {
  const CashPositionScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('Cash position')),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(cashPositionProvider.future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              ref.watch(cashPositionProvider),
              onRetry: () => ref.invalidate(cashPositionProvider),
              builder: (data) {
                final flow = data['cash_flow'] as Json;
                final net = parseNum(data['net_position']);
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    StatGrid(
                      tiles: [
                        StatTile(
                          label: 'Cash in hand',
                          value: rupees(parseNum(data['cash_in_hand'])),
                        ),
                        StatTile(
                          label: 'Bank',
                          value: rupees(parseNum(data['bank_balance'])),
                        ),
                        StatTile(
                          label: 'Total',
                          value: rupees(parseNum(data['total'])),
                        ),
                      ],
                    ),
                    const SectionTitle('Owed to and by you'),
                    StatGrid(
                      tiles: [
                        StatTile(
                          label: 'To receive',
                          value: rupees(parseNum(data['total_receivable'])),
                          color: AppColors.sale,
                        ),
                        StatTile(
                          label: 'To pay',
                          value: rupees(parseNum(data['total_payable'])),
                          color: AppColors.expense,
                        ),
                        StatTile(
                          label: 'Net position',
                          value: rupees(net),
                          color: net >= 0 ? AppColors.sale : AppColors.expense,
                        ),
                      ],
                    ),
                    SectionTitle('Cash flow · ${data['period_label']}'),
                    StatGrid(
                      tiles: [
                        StatTile(
                          label: 'Opening',
                          value: rupees(parseNum(flow['opening_cash'])),
                        ),
                        StatTile(
                          label: 'From revenue',
                          value: rupees(parseNum(flow['cash_in_from_sales'])),
                          color: AppColors.sale,
                        ),
                        StatTile(
                          label: 'Purchases',
                          value: rupees(parseNum(flow['cash_out_purchases'])),
                          color: AppColors.purchase,
                        ),
                        StatTile(
                          label: 'Expenses',
                          value: rupees(parseNum(flow['cash_out_expenses'])),
                          color: AppColors.expense,
                        ),
                        StatTile(
                          label: 'Net change',
                          value: rupees(parseNum(flow['net_change_in_cash'])),
                        ),
                        StatTile(
                          label: 'Closing',
                          value: rupees(parseNum(flow['closing_cash'])),
                        ),
                      ],
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
