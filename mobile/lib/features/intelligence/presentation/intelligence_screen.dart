import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

enum _Section { analytics, revenue, cost }

/// Business analytics, revenue insights and cost insights for a chosen period.
class IntelligenceScreen extends ConsumerStatefulWidget {
  const IntelligenceScreen({super.key});

  @override
  ConsumerState<IntelligenceScreen> createState() => _IntelligenceScreenState();
}

class _IntelligenceScreenState extends ConsumerState<IntelligenceScreen> {
  String _period = 'this_month';
  _Section _section = _Section.analytics;

  @override
  Widget build(BuildContext context) {
    return ReportPage(
      title: 'Intelligence',
      actions: [
        IconButton(
          tooltip: 'Category statement',
          icon: const Icon(Icons.fact_check_outlined),
          onPressed: () => context.push(AppRoutes.categoryStatement),
        ),
      ],
      onRefresh: () => ref.refresh(intelligenceProvider(_period).future),
      tabs: TabPills<_Section>(
        selected: _section,
        onChanged: (v) => setState(() => _section = v),
        items: const [
          TabPillItem(
            value: _Section.analytics,
            label: 'Analytics',
            icon: Icons.insights_outlined,
          ),
          TabPillItem(
            value: _Section.revenue,
            label: 'Revenue',
            icon: Icons.south_west,
            color: AppColors.sale,
          ),
          TabPillItem(
            value: _Section.cost,
            label: 'Cost',
            icon: Icons.north_east,
            color: AppColors.expense,
          ),
        ],
      ),
      filters: [
        FilterRow(
          children: [
            PeriodDropdown(
              value: _period,
              onChanged: (v) => setState(() => _period = v),
            ),
          ],
        ),
      ],
      children: [
        asyncBody(
          ref.watch(intelligenceProvider(_period)),
          onRetry: () => ref.invalidate(intelligenceProvider(_period)),
          builder: (data) => switch (_section) {
            _Section.analytics => _Analytics(data: data),
            _Section.revenue => _Revenue(data: data),
            _Section.cost => _Cost(data: data),
          },
        ),
      ],
    );
  }
}

num _sumQuantity(List items) =>
    items.fold<num>(0, (a, r) => a + parseNum((r as Map)['quantity']));

class _Analytics extends StatelessWidget {
  const _Analytics({required this.data});

  final Json data;

  @override
  Widget build(BuildContext context) {
    final kpis = data['kpis'] as Json;
    final weekdays = (data['weekday_averages'] as List).cast<Json>();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        HeroCard(
          eyebrow: 'Net profit · ${(data['period'] as Json)['label']}',
          value: rupees(parseNum(kpis['net_profit'])),
          delta: kpis['net_growth_pct'],
          caption:
              'Margin ${kpis['margin_pct']}% · Cash + bank ${rupees(parseNum(kpis['cash_balance']))}',
          figures: [
            (
              label: 'Revenue',
              value: rupeesShort(parseNum(kpis['sales'])),
              icon: Icons.south_west,
            ),
            (
              label: 'Expense',
              value: rupeesShort(parseNum(kpis['expenses'])),
              icon: Icons.north_east,
            ),
            (
              label: 'Purchase',
              value: rupeesShort(parseNum(kpis['purchases'])),
              icon: Icons.shopping_bag_outlined,
            ),
          ],
        ),
        const SectionTitle('Last 6 months'),
        TrendBars(
          points: (data['monthly_trend'] as List).cast<Json>(),
          labelOf: (p) => '${p['label']}'.split(' ').first,
          onPointTap: (p) => context.push(
            dailyRangeLocation(from: '${p['from']}', to: '${p['to']}'),
          ),
        ),
        const SectionTitle('Average revenue by weekday'),
        BreakdownBars(
          items: [
            for (final d in weekdays)
              {'name': d['day'], 'amount': d['average']},
          ],
          color: AppColors.brand,
          limit: 7,
        ),
        const SectionTitle('Cash vs bank'),
        BreakdownBars(
          items: data['payment_modes'] as List,
          color: AppColors.brand,
        ),
        const SectionTitle('Activity by sub-category'),
        _Activity(
          groups: (data['activity_by_subcategory'] as List? ?? const [])
              .cast<Json>(),
        ),
      ],
    );
  }
}

/// Quantity recorded per sub-category, one card per category.
class _Activity extends StatelessWidget {
  const _Activity({required this.groups});

  final List<Json> groups;

  @override
  Widget build(BuildContext context) {
    if (groups.isEmpty) {
      return const ReportCard(
        child: Text('No quantity recorded for this period.'),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final g in groups) ...[
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 8, 4, 6),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '${g['category']}',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  '${g['total']} count',
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          ReportCard(
            child: Column(
              children: [
                for (final item in (g['items'] as List).cast<Json>())
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            '${item['name']}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        QuantityPill(parseNum(item['quantity'])),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

/// Opens the entries of a category over the period the screen is showing.
void _open(BuildContext context, Json data, String kind, String name) {
  final period = data['period'] as Json;
  context.push(
    categoryLocation(
      kind: kind,
      name: name,
      from: '${period['from']}',
      to: '${period['to']}',
    ),
  );
}

class _Revenue extends StatelessWidget {
  const _Revenue({required this.data});

  final Json data;

  @override
  Widget build(BuildContext context) {
    final gdn = data['gross_discount_net'] as Json;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        HeroCard(
          eyebrow: 'Net revenue · ${(data['period'] as Json)['label']}',
          value: rupees(parseNum(gdn['net'])),
          caption:
              'Quantity ${_sumQuantity(data['revenue_by_channel'] as List).round()}',
          figures: [
            (
              label: 'Gross',
              value: rupeesShort(parseNum(gdn['gross'])),
              icon: Icons.receipt_long_outlined,
            ),
            (
              label: 'Discount',
              value: rupeesShort(parseNum(gdn['discount'])),
              icon: Icons.sell_outlined,
            ),
            (
              label: 'Net',
              value: rupeesShort(parseNum(gdn['net'])),
              icon: Icons.south_west,
            ),
          ],
        ),
        const SectionTitle('Revenue by category'),
        BreakdownBars(
          items: data['revenue_by_channel'] as List,
          color: AppColors.sale,
          limit: 12,
          onItemTap: (name) => _open(context, data, 'sale', name),
        ),
        _QuantitySection(
          data: data,
          onOpen: (name) {
            final period = data['period'] as Json;
            context.push(
              quantityLocation(
                kind: 'sale',
                name: name,
                from: '${period['from']}',
                to: '${period['to']}',
              ),
            );
          },
        ),
      ],
    );
  }
}

class _Cost extends StatelessWidget {
  const _Cost({required this.data});

  final Json data;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      HeroCard(
        eyebrow: 'Total cost · ${(data['period'] as Json)['label']}',
        value: rupees(
          parseNum((data['kpis'] as Json)['expenses']) +
              parseNum((data['kpis'] as Json)['purchases']),
        ),
        caption:
            'Quantity purchased ${_sumQuantity(data['purchase_by_category'] as List).round()}',
        figures: [
          (
            label: 'Expense',
            value: rupeesShort(parseNum((data['kpis'] as Json)['expenses'])),
            icon: Icons.north_east,
          ),
          (
            label: 'Purchase',
            value: rupeesShort(parseNum((data['kpis'] as Json)['purchases'])),
            icon: Icons.shopping_bag_outlined,
          ),
        ],
      ),
      const SectionTitle('Expenses by category'),
      BreakdownBars(
        items: data['expense_by_category'] as List,
        color: AppColors.expense,
        limit: 12,
        onItemTap: (name) => _open(context, data, 'expense', name),
      ),
      const SectionTitle('Purchases by category'),
      BreakdownBars(
        items: data['purchase_by_category'] as List,
        color: AppColors.purchase,
        limit: 12,
        onItemTap: (name) => _open(context, data, 'purchase', name),
      ),
    ],
  );
}

/// Quantity sold per category: chart, insights and a details table.
class _QuantitySection extends StatelessWidget {
  const _QuantitySection({required this.data, required this.onOpen});

  final Json data;
  final ValueChanged<String> onOpen;

  @override
  Widget build(BuildContext context) {
    final rows =
        (data['revenue_by_channel'] as List)
            .cast<Json>()
            .where((r) => parseNum(r['quantity']) > 0)
            .toList()
          ..sort(
            (a, b) =>
                parseNum(b['quantity']).compareTo(parseNum(a['quantity'])),
          );

    if (rows.isEmpty) {
      return const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionTitle('Quantity by category'),
          ReportCard(
            child: Text(
              'No quantities were recorded on revenue entries in this period.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          ),
        ],
      );
    }

    final totalQty = rows.fold<num>(0, (a, r) => a + parseNum(r['quantity']));
    final totalAmount = rows.fold<num>(0, (a, r) => a + parseNum(r['amount']));
    num perUnit(Json r) => parseNum(r['amount']) / parseNum(r['quantity']);
    final top = rows.first;
    final byPrice = [...rows]..sort((a, b) => perUnit(b).compareTo(perUnit(a)));
    final dearest = byPrice.first;
    final cheapest = byPrice.last;
    final allRows = (data['revenue_by_channel'] as List).cast<Json>();
    final missing = allRows.length - rows.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(
          'Quantity by category',
          trailing: Text(
            '${totalQty.round()} units',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
        ),
        BreakdownBars(
          items: rows,
          color: AppColors.brand,
          limit: 12,
          quantityMode: true,
          onItemTap: onOpen,
        ),
        const SectionTitle('Insights'),
        ReportCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _Insight(
                icon: Icons.emoji_events_outlined,
                text:
                    '${top['name']} sold the most: ${parseNum(top['quantity']).round()} units, '
                    '${(parseNum(top['quantity']) / totalQty * 100).round()}% of all units.',
              ),
              _Insight(
                icon: Icons.payments_outlined,
                text:
                    'On average each unit brought in ${rupees(totalAmount / totalQty)}.',
              ),
              if (rows.length > 1) ...[
                _Insight(
                  icon: Icons.trending_up,
                  text:
                      '${dearest['name']} earns the most per unit (${rupees(perUnit(dearest))}).',
                ),
                _Insight(
                  icon: Icons.trending_down,
                  text:
                      '${cheapest['name']} earns the least per unit (${rupees(perUnit(cheapest))}).',
                ),
              ],
              if (missing > 0)
                _Insight(
                  icon: Icons.info_outline,
                  text:
                      '$missing ${missing == 1 ? 'category has' : 'categories have'} revenue but no quantity recorded.',
                ),
            ],
          ),
        ),
        const SectionTitle('Quantity details'),
        ReportCard(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(
            children: [
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 8, 16, 6),
                child: Row(
                  children: [
                    Expanded(child: _Head('Category')),
                    SizedBox(width: 56, child: _Head('Qty', right: true)),
                    SizedBox(width: 92, child: _Head('Revenue', right: true)),
                    SizedBox(width: 78, child: _Head('Per unit', right: true)),
                  ],
                ),
              ),
              for (final r in rows)
                InkWell(
                  onTap: () => onOpen('${r['name']}'),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 9,
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            '${r['name']}',
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        SizedBox(
                          width: 56,
                          child: Text(
                            '${parseNum(r['quantity']).round()}',
                            textAlign: TextAlign.right,
                            style: const TextStyle(fontWeight: FontWeight.w800),
                          ),
                        ),
                        SizedBox(
                          width: 92,
                          child: Text(
                            rupeesShort(parseNum(r['amount'])),
                            textAlign: TextAlign.right,
                          ),
                        ),
                        SizedBox(
                          width: 78,
                          child: Text(
                            rupeesShort(perUnit(r)),
                            textAlign: TextAlign.right,
                            style: const TextStyle(
                              color: AppColors.textSecondary,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              const Divider(height: 14, color: AppColors.divider),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                child: Row(
                  children: [
                    const Expanded(
                      child: Text(
                        'Total',
                        style: TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                    SizedBox(
                      width: 56,
                      child: Text(
                        '${totalQty.round()}',
                        textAlign: TextAlign.right,
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                    SizedBox(
                      width: 92,
                      child: Text(
                        rupeesShort(totalAmount),
                        textAlign: TextAlign.right,
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                    SizedBox(
                      width: 78,
                      child: Text(
                        rupeesShort(totalAmount / totalQty),
                        textAlign: TextAlign.right,
                        style: const TextStyle(fontWeight: FontWeight.w800),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Head extends StatelessWidget {
  const _Head(this.text, {this.right = false});

  final String text;
  final bool right;

  @override
  Widget build(BuildContext context) => FittedBox(
    fit: BoxFit.scaleDown,
    alignment: right ? Alignment.centerRight : Alignment.centerLeft,
    child: Text(
      text.toUpperCase(),
      textAlign: right ? TextAlign.right : TextAlign.left,
      style: const TextStyle(
        fontSize: 10,
        fontWeight: FontWeight.w800,
        letterSpacing: 0.4,
        color: AppColors.textSecondary,
      ),
    ),
  );
}

class _Insight extends StatelessWidget {
  const _Insight({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 6),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 18, color: AppColors.brand),
        const SizedBox(width: 10),
        Expanded(
          child: Text(text, style: const TextStyle(fontSize: 14, height: 1.35)),
        ),
      ],
    ),
  );
}
