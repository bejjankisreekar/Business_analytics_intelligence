import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../auth/application/session_controller.dart';

/// Everything that doesn't have a tab of its own, plus the account.
class MoreScreen extends ConsumerWidget {
  const MoreScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(sessionControllerProvider).user;

    Widget group(String title, List<Widget> items) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(title),
        ReportCard(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(children: items),
        ),
      ],
    );

    Widget item(
      IconData icon,
      Color color,
      String title,
      String subtitle,
      String route,
    ) => InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: () => context.push(route),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Row(
          children: [
            IconBadge(icon: icon, color: color, size: 42),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      fontWeight: FontWeight.w700,
                      fontSize: 15,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    subtitle,
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            const TapChevron(),
          ],
        ),
      ),
    );

    final initial = (user?.displayName ?? '?').characters.first.toUpperCase();

    return Scaffold(
      appBar: AppBar(title: const Text('More')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
        children: [
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              gradient: AppColors.heroGradient,
              borderRadius: BorderRadius.circular(24),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x554338CA),
                  blurRadius: 28,
                  offset: Offset(0, 14),
                ),
              ],
            ),
            child: Row(
              children: [
                CircleAvatar(
                  radius: 28,
                  backgroundColor: Colors.white.withValues(alpha: 0.2),
                  child: Text(
                    initial,
                    style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                      color: Colors.white,
                    ),
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        user?.displayName ?? '',
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w800,
                          color: Colors.white,
                        ),
                      ),
                      Text(
                        user?.email ?? '',
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.8),
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        [
                          user?.organizationName ?? '',
                          if ((user?.role ?? '').isNotEmpty) user!.role,
                        ].join(' · '),
                        style: TextStyle(
                          fontSize: 12,
                          color: Colors.white.withValues(alpha: 0.75),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          group('Reports', [
            item(
              Icons.calendar_month_outlined,
              AppColors.brand,
              'Daily book',
              'Entries recorded on any day',
              AppRoutes.dailyBook,
            ),
            item(
              Icons.grid_view_outlined,
              AppColors.accent,
              'Monthly summary',
              'The last 12 months, tap a month for its days',
              AppRoutes.monthlySummary,
            ),
            item(
              Icons.fact_check_outlined,
              AppColors.expense,
              'Category statement',
              'Every entry and quantity behind a category',
              AppRoutes.categoryStatement,
            ),
            item(
              Icons.receipt_long_outlined,
              AppColors.sale,
              'Financial reports',
              'P&L, balance sheet, cash flow, GST',
              AppRoutes.financialReports,
            ),
          ]),
          group('Money', [
            item(
              Icons.account_balance_wallet_outlined,
              AppColors.purchase,
              'Cash position',
              'Cash, bank, receivables and payables',
              AppRoutes.cashPosition,
            ),
            item(
              Icons.account_balance_outlined,
              AppColors.brand,
              'Bank accounts',
              'Balance and ledger of each account',
              AppRoutes.bankAccounts,
            ),
            item(
              Icons.schedule_outlined,
              AppColors.expense,
              'Receivables & payables',
              'Who owes what, by age',
              AppRoutes.aging,
            ),
            item(
              Icons.storefront_outlined,
              AppColors.accent,
              'Vendors',
              'Suppliers, amounts owed and ledgers',
              AppRoutes.vendors,
            ),
            item(
              Icons.workspace_premium_outlined,
              AppColors.brand,
              'Billing',
              'Your plan, invoices and payments',
              AppRoutes.billing,
            ),
          ]),
          const SizedBox(height: 20),
          OutlinedButton.icon(
            onPressed: () =>
                ref.read(sessionControllerProvider.notifier).signOut(),
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(50),
              foregroundColor: AppColors.danger,
              backgroundColor: AppColors.card,
              side: const BorderSide(color: AppColors.divider),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
