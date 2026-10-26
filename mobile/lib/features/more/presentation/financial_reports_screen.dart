import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

enum _Statement { pnl, balance, cashFlow, gst }

/// Profit & loss, balance sheet, cash flow and GST for a chosen period.
class FinancialReportsScreen extends ConsumerStatefulWidget {
  const FinancialReportsScreen({super.key});

  @override
  ConsumerState<FinancialReportsScreen> createState() =>
      _FinancialReportsScreenState();
}

class _FinancialReportsScreenState
    extends ConsumerState<FinancialReportsScreen> {
  String _period = 'this_month';
  String _flow = 'all';
  _Statement _statement = _Statement.pnl;

  @override
  Widget build(BuildContext context) {
    return ReportPage(
      title: 'Financial reports',
      onRefresh: () =>
          ref.refresh(financialReportsProvider((_period, _flow)).future),
      tabs: TabPills<_Statement>(
        selected: _statement,
        onChanged: (v) => setState(() => _statement = v),
        items: const [
          TabPillItem(
            value: _Statement.pnl,
            label: 'P&L',
            icon: Icons.trending_up,
          ),
          TabPillItem(
            value: _Statement.balance,
            label: 'Balance',
            icon: Icons.balance_outlined,
          ),
          TabPillItem(
            value: _Statement.cashFlow,
            label: 'Cash',
            icon: Icons.payments_outlined,
          ),
          TabPillItem(
            value: _Statement.gst,
            label: 'GST',
            icon: Icons.receipt_long_outlined,
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
            if (_statement == _Statement.cashFlow)
              DropdownField(
                label: 'Show',
                icon: Icons.payments_outlined,
                value: _flow,
                items: const {
                  'all': 'Cash + online',
                  'cash': 'Cash only',
                  'bank': 'Online only',
                },
                onChanged: (v) => setState(() => _flow = v ?? _flow),
              ),
          ],
        ),
      ],
      children: [
        asyncBody(
          ref.watch(financialReportsProvider((_period, _flow))),
          onRetry: () =>
              ref.invalidate(financialReportsProvider((_period, _flow))),
          builder: (data) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _Hero(statement: _statement, data: data),
              const SizedBox(height: 4),
              switch (_statement) {
                _Statement.pnl => _Pnl(
                  data['pnl'] as Json,
                  data['period'] as Json,
                ),
                _Statement.balance => _BalanceSheet(
                  data['balance_sheet'] as Json,
                ),
                _Statement.cashFlow => _CashFlow(data['cash_flow'] as Json),
                _Statement.gst => _Gst(data['gst'] as Json),
              },
            ],
          ),
        ),
      ],
    );
  }
}

/// Headline card for the selected statement.
class _Hero extends StatelessWidget {
  const _Hero({required this.statement, required this.data});

  final _Statement statement;
  final Json data;

  @override
  Widget build(BuildContext context) {
    final label = '${(data['period'] as Json)['label']}';
    return switch (statement) {
      _Statement.pnl => () {
        final pnl = data['pnl'] as Json;
        return HeroCard(
          eyebrow: 'Net profit · $label',
          value: _money(pnl, 'net_profit'),
          caption: pnl['net_margin_pct'] == null
              ? null
              : 'Net margin ${pnl['net_margin_pct']}%',
          figures: [
            (
              label: 'Revenue',
              value: rupeesShort(parseNum(pnl['revenue'])),
              icon: Icons.south_west,
            ),
            (
              label: 'Purchases',
              value: rupeesShort(parseNum(pnl['cogs'])),
              icon: Icons.shopping_bag_outlined,
            ),
            (
              label: 'Expenses',
              value: rupeesShort(parseNum(pnl['total_opex'])),
              icon: Icons.north_east,
            ),
          ],
        );
      }(),
      _Statement.balance => () {
        final bs = data['balance_sheet'] as Json;
        return HeroCard(
          eyebrow: 'Total assets · As of ${bs['as_of']}',
          value: _money(bs, 'total_assets'),
          figures: [
            (
              label: 'Cash',
              value: rupeesShort(parseNum(bs['cash'])),
              icon: Icons.payments_outlined,
            ),
            (
              label: 'Liabilities',
              value: rupeesShort(parseNum(bs['total_liabilities'])),
              icon: Icons.schedule_outlined,
            ),
            (
              label: 'Equity',
              value: rupeesShort(parseNum(bs['total_equity'])),
              icon: Icons.account_balance_outlined,
            ),
          ],
        );
      }(),
      _Statement.cashFlow => () {
        final cf = data['cash_flow'] as Json;
        return HeroCard(
          eyebrow:
              'Closing balance · ${cf['mode_label'] ?? 'Cash and online'} · $label',
          value: _money(cf, 'closing_cash'),
          caption: 'Opening ${_money(cf, 'opening_cash')}',
          figures: [
            (
              label: 'In',
              value: rupeesShort(parseNum(cf['cash_in_from_sales'])),
              icon: Icons.south_west,
            ),
            (
              label: 'Purchases',
              value: rupeesShort(parseNum(cf['cash_out_purchases'])),
              icon: Icons.shopping_bag_outlined,
            ),
            (
              label: 'Expenses',
              value: rupeesShort(parseNum(cf['cash_out_expenses'])),
              icon: Icons.north_east,
            ),
          ],
        );
      }(),
      _Statement.gst => () {
        final gst = data['gst'] as Json;
        final payable = parseNum(gst['net_gst_payable']);
        return HeroCard(
          eyebrow: '${payable >= 0 ? 'GST payable' : 'GST credit'} · $label',
          value: rupees(payable.abs()),
          figures: [
            (
              label: 'Output',
              value: rupeesShort(parseNum(gst['output_tax'])),
              icon: Icons.north_east,
            ),
            (
              label: 'Input',
              value: rupeesShort(parseNum(gst['input_tax'])),
              icon: Icons.south_west,
            ),
          ],
        );
      }(),
    };
  }
}

String _money(Json data, String key) => rupees(parseNum(data[key]));

Color _signed(num value) => value >= 0 ? AppColors.sale : AppColors.expense;

class _Pnl extends StatelessWidget {
  const _Pnl(this.data, this.period);

  final Json data;
  final Json period;

  @override
  Widget build(BuildContext context) {
    final net = parseNum(data['net_profit']);
    final expenses = (data['expenses'] as List).cast<Json>();
    return ReportCard(
      child: Column(
        children: [
          LineRow('Revenue', _money(data, 'revenue'), color: AppColors.sale),
          LineRow('Cost of purchases', _money(data, 'cogs')),
          const Divider(),
          LineRow('Gross profit', _money(data, 'gross_profit'), bold: true),
          const SizedBox(height: 8),
          for (final e in expenses)
            LineRow(
              '${e['name']}',
              rupees(parseNum(e['amount'])),
              onTap: () => context.push(
                categoryLocation(
                  kind: 'expense',
                  name: '${e['name']}',
                  from: '${period['from']}',
                  to: '${period['to']}',
                ),
              ),
            ),
          LineRow(
            'Total expenses',
            _money(data, 'total_opex'),
            bold: true,
            color: AppColors.expense,
          ),
          const Divider(),
          LineRow('Net profit', rupees(net), bold: true, color: _signed(net)),
          LineRow(
            'Net margin',
            data['net_margin_pct'] == null ? '—' : '${data['net_margin_pct']}%',
          ),
          const Divider(),
          LineRow('Previous revenue', _money(data, 'prev_revenue')),
          LineRow('Previous net profit', _money(data, 'prev_net_profit')),
        ],
      ),
    );
  }
}

class _BalanceSheet extends StatelessWidget {
  const _BalanceSheet(this.data);

  final Json data;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ReportCard(
          child: Column(
            children: [
              LineRow('Cash in hand', _money(data, 'cash')),
              LineRow('Bank', _money(data, 'bank')),
              LineRow('Total assets', _money(data, 'total_assets'), bold: true),
            ],
          ),
        ),
        const SectionTitle('Liabilities & equity'),
        ReportCard(
          child: Column(
            children: [
              LineRow('Liabilities', _money(data, 'total_liabilities')),
              LineRow('Opening capital', _money(data, 'opening_balance')),
              LineRow('Retained earnings', _money(data, 'retained_earnings')),
              LineRow('Partner capital', _money(data, 'partner_capital')),
              LineRow('Total equity', _money(data, 'total_equity'), bold: true),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(
            data['balances'] == true
                ? 'Assets equal liabilities + equity.'
                : 'Assets and equity are out of balance.',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
        ),
      ],
    );
  }
}

class _CashFlow extends StatelessWidget {
  const _CashFlow(this.data);

  final Json data;

  @override
  Widget build(BuildContext context) {
    return ReportCard(
      child: Column(
        children: [
          LineRow(
            'Showing',
            '${data['mode_label'] ?? 'Cash and online'}',
            bold: true,
          ),
          LineRow('Opening balance', _money(data, 'opening_cash')),
          LineRow(
            'From revenue',
            _money(data, 'cash_in_from_sales'),
            color: AppColors.sale,
          ),
          LineRow('Partners invested', _money(data, 'partner_invested')),
          if (data['mode'] != null && data['mode'] != 'all') ...[
            LineRow(
              'Transferred in',
              _money(data, 'transfer_in'),
              color: AppColors.sale,
            ),
            LineRow(
              'Transferred out',
              _money(data, 'transfer_out'),
              color: AppColors.expense,
            ),
          ],
          LineRow(
            'Purchases',
            _money(data, 'cash_out_purchases'),
            color: AppColors.purchase,
          ),
          LineRow(
            'Expenses',
            _money(data, 'cash_out_expenses'),
            color: AppColors.expense,
          ),
          LineRow('Partners withdrawn', _money(data, 'partner_withdrawn')),
          const Divider(),
          LineRow(
            'Net change',
            _money(data, 'net_change_in_cash'),
            bold: true,
            color: _signed(parseNum(data['net_change_in_cash'])),
          ),
          LineRow('Closing balance', _money(data, 'closing_cash'), bold: true),
        ],
      ),
    );
  }
}

class _Gst extends StatelessWidget {
  const _Gst(this.data);

  final Json data;

  @override
  Widget build(BuildContext context) {
    final payable = parseNum(data['net_gst_payable']);

    Widget rates(String title, List<Json> rows) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(title),
        ReportCard(
          child: Column(
            children: [
              for (final r in rows)
                LineRow(
                  '${r['rate']}% on ${rupees(parseNum(r['taxable_value']))}',
                  rupees(parseNum(r['tax'])),
                ),
              if (rows.isEmpty) const LineRow('No taxed entries', '—'),
            ],
          ),
        ),
      ],
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        StatGrid(
          tiles: [
            StatTile(
              label: 'Output tax',
              value: _money(data, 'output_tax'),
              color: AppColors.expense,
            ),
            StatTile(
              label: 'Input credit',
              value: _money(data, 'input_tax'),
              color: AppColors.sale,
            ),
            StatTile(
              label: payable >= 0 ? 'GST payable' : 'GST credit',
              value: rupees(payable.abs()),
            ),
          ],
        ),
        rates(
          'Output tax on revenue',
          (data['sales_rows'] as List).cast<Json>(),
        ),
        rates(
          'Input credit on purchases',
          (data['purchase_rows'] as List).cast<Json>(),
        ),
        const SectionTitle('Not taxed'),
        ReportCard(
          child: Column(
            children: [
              LineRow('Revenue', _money(data, 'untaxed_sales')),
              LineRow('Purchases', _money(data, 'untaxed_purchases')),
            ],
          ),
        ),
      ],
    );
  }
}
