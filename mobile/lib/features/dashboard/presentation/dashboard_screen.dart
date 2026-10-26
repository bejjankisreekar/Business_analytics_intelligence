import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../auth/application/session_controller.dart';
import '../../entries/models/entry_type.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// The full dashboard: today and month at a glance, KPIs, cash, the week, breakdowns and recent entries.
String _greeting() {
  final hour = DateTime.now().hour;
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(sessionControllerProvider).user;
    final dashboard = ref.watch(dashboardProvider);

    return Scaffold(
      appBar: AppBar(
        toolbarHeight: 84,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${_greeting()}${(user?.name ?? '').isEmpty ? '' : ', ${user!.name.split(' ').first}'}',
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w500,
                color: AppColors.textSecondary,
              ),
            ),
            Text(user?.organizationName ?? ''),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: CircleAvatar(
              backgroundColor: AppColors.brandSoft,
              child: Text(
                (user?.displayName ?? '?').characters.first.toUpperCase(),
                style: const TextStyle(
                  color: AppColors.brand,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ],
      ),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(dashboardProvider.future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              dashboard,
              onRetry: () => ref.invalidate(dashboardProvider),
              builder: (data) => _Content(data: data),
            ),
          ],
        ),
      ),
    );
  }
}

class _Content extends StatelessWidget {
  const _Content({required this.data});

  final Json data;

  static const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  @override
  Widget build(BuildContext context) {
    final kpis = data['kpis'] as Json;
    final month = data['month'] as Json;
    final today = data['today'] as Json;
    final top = data['top_categories'] as Json;
    final recent = (data['recent_entries'] as List).cast<Json>();
    final now = DateTime.now();
    final monthFrom = isoDate(DateTime(now.year, now.month, 1));
    final monthTo = isoDate(now);
    void openCategory(String kind, String name) => context.push(
      categoryLocation(kind: kind, name: name, from: monthFrom, to: monthTo),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        HeroCard(
          eyebrow: 'Net profit · ${data['period_label']}',
          value: rupees(parseNum(month['net'])),
          delta: kpis['net_growth_pct'],
          caption:
              'Margin ${kpis['margin_pct']}%  ·  change vs previous period',
          figures: [
            (
              label: 'Revenue',
              value: rupeesShort(parseNum(month['sale'])),
              icon: Icons.south_west,
            ),
            (
              label: 'Expense',
              value: rupeesShort(parseNum(month['expense'])),
              icon: Icons.north_east,
            ),
            (
              label: 'Purchase',
              value: rupeesShort(parseNum(month['purchase'])),
              icon: Icons.shopping_bag_outlined,
            ),
          ],
        ),
        const SectionTitle('Today'),
        _TodayStrip(
          totals: today,
          onTap: () => context.push(dayLocation(isoDate(DateTime.now()))),
        ),
        const SectionTitle('Key numbers'),
        StatGrid(
          tiles: [
            StatTile(
              label: 'Revenue growth',
              value: kpis['sales_growth_pct'] == null
                  ? '—'
                  : '${kpis['sales_growth_pct']}%',
              icon: Icons.trending_up,
              color: AppColors.sale,
            ),
            StatTile(
              label: 'Profit margin',
              value: '${kpis['margin_pct']}%',
              icon: Icons.percent,
              color: AppColors.accent,
            ),
            StatTile(
              label: 'Cash in hand',
              value: rupees(parseNum(data['cash_in_hand'])),
              icon: Icons.payments_outlined,
              color: AppColors.purchase,
            ),
            StatTile(
              label: 'Bank balance',
              value: rupees(parseNum(data['bank_balance'])),
              icon: Icons.account_balance_outlined,
              color: AppColors.brand,
            ),
          ],
        ),
        const SectionTitle('Last 7 days'),
        TrendBars(
          points: (data['last_7_days'] as List).cast<Json>(),
          labelOf: (p) =>
              _weekdays[DateTime.parse(p['date'] as String).weekday - 1],
          onPointTap: (p) => context.push(dayLocation(p['date'] as String)),
        ),
        const SectionTitle('This month, day by day'),
        LineTrend(
          points: (data['daily'] as List).cast<Json>(),
          labelOf: (p) =>
              displayDate(DateTime.parse(p['date'] as String))
                  .split(' ')
                  .take(2)
                  .join(' '),
        ),
        const SectionTitle('Where the money goes'),
        DonutBreakdown(
          items: data['expense_breakdown'] as List,
          centerLabel: 'Expenses',
          onItemTap: (name) => openCategory('expense', name),
        ),
        for (final type in EntryType.values)
          if (((top[type.apiValue] as List?) ?? const []).isNotEmpty) ...[
            SectionTitle('Top ${type.label.toLowerCase()} categories'),
            BreakdownBars(
              items: top[type.apiValue] as List,
              color: type.color,
              limit: 5,
              onItemTap: (name) => openCategory(type.apiValue, name),
            ),
          ],
        const SectionTitle('Recent entries'),
        if (recent.isEmpty)
          const ReportCard(
            child: Text(
              'No entries yet.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          )
        else
          ReportCard(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              children: [
                for (final entry in recent)
                  _RecentTile(
                    entry: entry,
                    onTap: () => context.push(dayLocation('${entry['date']}')),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

IconData _iconFor(EntryType type) => switch (type) {
  EntryType.sale => Icons.south_west,
  EntryType.expense => Icons.north_east,
  EntryType.purchase => Icons.shopping_bag_outlined,
};

class _TodayStrip extends StatelessWidget {
  const _TodayStrip({required this.totals, required this.onTap});

  final Json totals;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final net = parseNum(totals['net']);
    Widget chip(EntryType type, String key) => Expanded(
      child: ReportCard(
        padding: const EdgeInsets.all(12),
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            IconBadge(icon: _iconFor(type), color: type.color, size: 30),
            const SizedBox(height: 10),
            Text(
              type.label,
              style: const TextStyle(
                fontSize: 11,
                color: AppColors.textSecondary,
              ),
            ),
            FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Text(
                rupees(parseNum(totals[key])),
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 15,
                ),
              ),
            ),
          ],
        ),
      ),
    );
    return Column(
      children: [
        Row(
          children: [
            chip(EntryType.sale, 'sale'),
            const SizedBox(width: 10),
            chip(EntryType.expense, 'expense'),
            const SizedBox(width: 10),
            chip(EntryType.purchase, 'purchase'),
          ],
        ),
        const SizedBox(height: 10),
        ReportCard(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          onTap: onTap,
          child: Row(
            children: [
              const Text(
                'Net today',
                style: TextStyle(
                  color: AppColors.textSecondary,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              Text(
                rupees(net),
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w800,
                  color: net >= 0 ? AppColors.sale : AppColors.expense,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _RecentTile extends StatelessWidget {
  const _RecentTile({required this.entry, required this.onTap});

  final Json entry;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final type = switch (entry['type']) {
      'Revenue' => EntryType.sale,
      'Expense' => EntryType.expense,
      _ => EntryType.purchase,
    };
    final note = '${entry['note']}';
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
          children: [
            IconBadge(icon: _iconFor(type), color: type.color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${entry['label']}',
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    [
                      displayDate(DateTime.parse(entry['date'] as String)),
                      if (note.isNotEmpty) note,
                    ].join(' · '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            Text(
              '${type.sign > 0 ? '+' : '−'}₹${formatAmount(parseNum(entry['amount']))}',
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w800,
                color: type.color,
              ),
            ),
            const TapChevron(),
          ],
        ),
      ),
    );
  }
}
