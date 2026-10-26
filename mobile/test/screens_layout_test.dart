import 'package:finday_mobile/app/theme/app_theme.dart';
import 'package:finday_mobile/features/auth/application/session_controller.dart';
import 'package:finday_mobile/features/auth/models/app_user.dart';
import 'package:finday_mobile/features/daily/presentation/category_detail_screen.dart';
import 'package:finday_mobile/features/daily/presentation/day_detail_screen.dart';
import 'package:finday_mobile/features/dashboard/presentation/dashboard_screen.dart';
import 'package:finday_mobile/features/more/presentation/ledger_screen.dart';
import 'package:finday_mobile/features/more/presentation/more_screen.dart';
import 'package:finday_mobile/features/daily/presentation/daily_performance_screen.dart';
import 'package:finday_mobile/features/intelligence/presentation/intelligence_screen.dart';
import 'package:finday_mobile/features/more/presentation/aging_screen.dart';
import 'package:finday_mobile/features/more/presentation/bank_accounts_screen.dart';
import 'package:finday_mobile/features/more/presentation/cash_position_screen.dart';
import 'package:finday_mobile/features/more/presentation/financial_reports_screen.dart';
import 'package:finday_mobile/features/more/presentation/monthly_summary_screen.dart';
import 'package:finday_mobile/features/more/presentation/billing_screen.dart';
import 'package:finday_mobile/features/daily/presentation/quantity_detail_screen.dart';
import 'package:finday_mobile/features/entries/application/entries_providers.dart';
import 'package:finday_mobile/features/entries/models/entry_options.dart';
import 'package:finday_mobile/features/entries/presentation/entry_screen.dart';
import 'package:finday_mobile/features/more/presentation/vendors_screen.dart';
import 'package:finday_mobile/features/reports/application/reports_providers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _day(int i) => {
  'date': '2026-10-${(i + 1).toString().padLeft(2, '0')}',
  'sale': '${1000 + i * 733}',
  'expense': '${300 + i * 91}',
  'purchase': '${200 + i * 57}',
  'net': '${500 + i * 585}',
};

final _dashboard = <String, dynamic>{
  'today': _day(3),
  'month': {
    'sale': '5225365',
    'expense': '1200000',
    'purchase': '800000',
    'net': '3225365',
  },
  'last_7_days': [for (var i = 0; i < 7; i++) _day(i)],
  'top_categories': {
    'sale': [
      {'name': 'Walk-in', 'amount': '900000'},
      {'name': 'Online', 'amount': '400000'},
    ],
    'expense': [
      {'name': 'Rent', 'amount': '120000'},
    ],
    'purchase': <dynamic>[],
  },
  'period_label': 'October 2026',
  'kpis': {
    'sales': '5225365',
    'expenses': '1200000',
    'purchases': '800000',
    'net_profit': '3225365',
    'margin_pct': '61.7',
    'cash_balance': '2500000',
    'sales_growth_pct': '12.5',
    'net_growth_pct': '-3.2',
  },
  'cash_in_hand': '100000',
  'bank_balance': '2400000',
  'daily': [for (var i = 0; i < 24; i++) _day(i)],
  'expense_breakdown': [
    for (var i = 0; i < 7; i++)
      {'name': 'Category $i', 'amount': '${100000 - i * 9000}'},
  ],
  'recent_entries': [
    {
      'type': 'Revenue',
      'date': '2026-10-24',
      'label': 'Walk-in',
      'amount': '1250',
      'note': 'Morning sales',
    },
    {
      'type': 'Expense',
      'date': '2026-10-24',
      'label': 'Rent',
      'amount': '12000',
      'note': '',
    },
    {
      'type': 'Purchase',
      'date': '2026-10-23',
      'label': 'Stock',
      'amount': '5200',
      'note': '',
    },
  ],
};

final _intelligence = <String, dynamic>{
  'period': {
    'key': 'this_month',
    'label': 'October 2026',
    'from': '2026-10-01',
    'to': '2026-10-25',
  },
  'period_choices': [
    {'key': 'today', 'label': 'Today'},
    {'key': 'this_month', 'label': 'This month'},
  ],
  'kpis': _dashboard['kpis'],
  'monthly_trend': [
    for (var i = 0; i < 6; i++)
      {
        ..._day(i),
        'label': 'May 2026',
        'from': '2026-05-01',
        'to': '2026-05-31',
      },
  ],
  'weekday_averages': [
    for (final d in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
      {'day': d, 'average': '2500'},
  ],
  'payment_modes': [
    {'name': 'Cash', 'amount': '1000'},
    {'name': 'Bank', 'amount': '3000'},
  ],
  'revenue_by_channel': [
    {'name': 'Walk-in', 'amount': '900000', 'quantity': 120},
    {'name': 'Online', 'amount': '400000', 'quantity': 30},
    {'name': 'Services', 'amount': '50000', 'quantity': 0},
  ],
  'expense_by_category': [
    {'name': 'Rent', 'amount': '120000'},
  ],
  'purchase_by_category': <dynamic>[],
  'gross_discount_net': {'gross': '110', 'discount': '10', 'net': '100'},
};

final _financialReports = <String, dynamic>{
  'period': {
    'key': 'this_month',
    'label': 'October 2026',
    'from': '2026-10-01',
    'to': '2026-10-25',
  },
  'period_choices': [
    {'key': 'today', 'label': 'Today'},
    {'key': 'this_month', 'label': 'This month'},
  ],
  'pnl': {
    'revenue': '1000',
    'cogs': '300',
    'gross_profit': '700',
    'expenses': [
      {'name': 'Rent', 'amount': '200'},
    ],
    'total_opex': '200',
    'net_profit': '500',
    'net_margin_pct': '50.0',
    'prev_revenue': '900',
    'prev_net_profit': '400',
  },
  'balance_sheet': {
    'as_of': '2026-10-25',
    'cash': '1',
    'bank': '2',
    'total_assets': '3',
    'total_liabilities': '0',
    'retained_earnings': '3',
    'partner_capital': '0',
    'total_equity': '3',
    'opening_balance': '0',
    'balances': true,
  },
  'cash_flow': {
    for (final k in [
      'opening_cash',
      'cash_in_from_sales',
      'cash_out_purchases',
      'cash_out_expenses',
      'partner_invested',
      'partner_withdrawn',
      'net_change_in_cash',
      'closing_cash',
    ])
      k: '10',
  },
  'gst': {
    'sales_rows': [
      {'rate': '18.00', 'taxable_value': '1000', 'tax': '180'},
    ],
    'purchase_rows': <dynamic>[],
    'taxable_sales': '1000',
    'taxable_purchases': '0',
    'untaxed_sales': '0',
    'untaxed_purchases': '300',
    'output_tax': '180',
    'input_tax': '0',
    'net_gst_payable': '180',
  },
};

Map<String, dynamic> _agingSide() => {
  'totals': {
    'total': '500',
    'current': '300',
    'd1_30': '200',
    'd31_60': '0',
    'd61_90': '0',
    'd90_plus': '0',
  },
  'rows': [
    {
      'name': 'Acme',
      'total': '500',
      'current': '300',
      'd1_30': '200',
      'd31_60': '0',
      'd61_90': '0',
      'd90_plus': '0',
    },
  ],
};

Future<void> _pump(WidgetTester tester, Widget screen) async {
  tester.view.physicalSize = const Size(360 * 3, 800 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        dashboardProvider.overrideWith((ref) async => _dashboard),
        dailyPerformanceProvider.overrideWith(
          (ref, range) async => {
            'from': range.$1,
            'to': range.$2,
            'days': [for (var i = 3; i >= 0; i--) _day(i)],
            'totals': _day(0),
          },
        ),
        intelligenceProvider.overrideWith((ref, period) async => _intelligence),
        billingProvider.overrideWith(
          (ref) async => {
            'organization': 'Acme',
            'currency': 'INR',
            'locked': true,
            'payment_hold': false,
            'outstanding': '2360',
            'pay_url': 'https://example.com/app/billing/',
            'subscription': {
              'plan': 'Business',
              'features': ['Reports', 'Bulk import'],
              'user_limit': 5,
              'business_limit': null,
              'status': 'PAYMENT_DUE',
              'status_label': 'Payment Due',
              'payment_status': 'PENDING',
              'payment_status_label': 'Pending',
              'billing_cycle': 'MONTHLY',
              'billing_cycle_label': 'Monthly',
              'start_date': '2026-09-01',
              'end_date': '2026-09-30',
              'trial_end_date': null,
              'price': '2000',
              'discount': '0',
              'tax': '360',
              'final_amount': '2360',
              'auto_renewal': true,
              'autopay_enabled': false,
              'is_complimentary': false,
            },
            'invoices': [
              {
                'id': 1,
                'number': 'INV-0001',
                'invoice_date': '2026-10-01',
                'due_date': '2026-10-08',
                'period_start': '2026-10-01',
                'period_end': '2026-10-31',
                'subtotal': '2000',
                'discount': '0',
                'tax': '360',
                'total': '2360',
                'amount_paid': '0',
                'amount_due': '2360',
                'status': 'OVERDUE',
                'status_label': 'Overdue',
              },
            ],
            'payments': [
              {
                'id': 'PAY-1',
                'amount': '2360',
                'method': 'UPI',
                'gateway': 'Razorpay',
                'date': '2026-09-02',
                'status': 'SUCCESS',
                'status_label': 'Success',
                'invoice': 'INV-0000',
              },
            ],
          },
        ),
        entryOptionsProvider.overrideWith(
          (ref) async => EntryOptions.fromJson({
            'categories': {
              'sale': [
                {
                  'id': '1',
                  'name': 'OPD Consultation',
                  'subcategories': [
                    {
                      'id': '10',
                      'name': 'Cardiology',
                      'children': [
                        {'id': '100', 'name': 'Dr. Rao'},
                      ],
                    },
                  ],
                },
              ],
              'expense': [],
              'purchase': [],
            },
            'payment_modes': [
              {'value': 'CASH', 'label': 'Cash'},
              {'value': 'BANK', 'label': 'Bank'},
            ],
            'bank_accounts': [
              {'id': '1', 'name': 'HDFC'},
            ],
            'vendors': ['Acme'],
          }),
        ),
        dayEntriesProvider.overrideWith((ref, date) async => []),
        monthlySummaryProvider.overrideWith(
          (ref, key) async => {
            'months': [
              {
                ..._day(1),
                'label': 'Oct 2026',
                'from': '2026-10-01',
                'to': '2026-10-25',
              },
            ],
            'kind': key.$1,
            'granularity': key.$2,
            'period': {
              'key': key.$3,
              'label': 'This financial year',
              'from': '2026-04-01',
              'to': '2026-10-25',
            },
            'labels': ['Aug 2026', 'Sep 2026', 'Oct 2026'],
            'truncated': false,
            'grand_total': '3000',
            'column_totals': [
              {'value': '1000', 'change': null},
              {'value': '1000', 'change': '0.0'},
              {'value': '1000', 'change': '0.0'},
            ],
            'yoy': {
              'current_total': '3000',
              'prior_total': '2000',
              'prior_from': '2025-04-01',
              'prior_to': '2025-10-25',
              'change_pct': '50.0',
            },
            'rows': [
              {
                'name': 'Walk-in',
                'total': '2000',
                'cells': [
                  {'value': '500', 'change': null},
                  {'value': '700', 'change': '40.0'},
                  {'value': '800', 'change': '14.3'},
                ],
                'subrows': [
                  {
                    'name': 'Consultation',
                    'total': '1500',
                    'cells': <dynamic>[],
                  },
                ],
              },
            ],
          },
        ),
        cashPositionProvider.overrideWith(
          (ref) async => {
            'cash_in_hand': '1',
            'bank_balance': '2',
            'total': '3',
            'total_receivable': '4',
            'total_payable': '5',
            'net_position': '-1',
            'period_label': 'October 2026',
            'cash_flow': {
              for (final k in [
                'opening_cash',
                'cash_in_from_sales',
                'cash_out_purchases',
                'cash_out_expenses',
                'net_change_in_cash',
                'closing_cash',
              ])
                k: '10',
            },
          },
        ),
        financialReportsProvider.overrideWith(
          (ref, key) async => _financialReports,
        ),
        bankAccountsProvider.overrideWith(
          (ref) async => {
            'accounts': [
              {
                'id': '1',
                'name': 'Current A/c',
                'bank': 'HDFC',
                'account_number': '1234',
                'is_active': true,
                'balance': '5000',
              },
            ],
            'total': '5000',
          },
        ),
        vendorsProvider.overrideWith(
          (ref) async => {
            'vendors': [
              {
                'id': '1',
                'name': 'Acme',
                'phone': '999',
                'details': '',
                'is_active': true,
                'outstanding': '500',
              },
            ],
            'total_outstanding': '500',
          },
        ),
        agingProvider.overrideWith(
          (ref) async => {
            'buckets': [
              {'key': 'current', 'label': 'Current'},
              {'key': 'd1_30', 'label': '1–30 days'},
              {'key': 'd31_60', 'label': '31–60 days'},
              {'key': 'd61_90', 'label': '61–90 days'},
              {'key': 'd90_plus', 'label': '90+ days'},
            ],
            'receivables': _agingSide(),
            'payables': _agingSide(),
          },
        ),
        dayDetailProvider.overrideWith(
          (ref, date) async => {
            'date': date,
            'opening_cash': '1',
            'opening_bank': '2',
            'closing_cash': '3',
            'closing_bank': '4',
            'totals': {
              'sale': '500',
              'expense': '100',
              'purchase': '50',
              'net': '350',
            },
            'entries': {
              'sale': [
                {
                  'id': '1',
                  'type': 'sale',
                  'category': 'Walk-in',
                  'subcategory': 'Tea',
                  'party': 'Ravi',
                  'amount': '500',
                  'payment_mode': 'Cash',
                  'note': 'Morning',
                },
              ],
              'expense': <dynamic>[],
              'purchase': <dynamic>[],
            },
          },
        ),
        categoryDetailProvider.overrideWith(
          (ref, key) async => {
            'name': key.$2,
            'kind': key.$1,
            'from': key.$3,
            'to': key.$4,
            'total': '500',
            'count': 1,
            'entries': [
              {
                'date': key.$3,
                'subcategory': 'Tea',
                'quantity': 4,
                'party': '',
                'amount': '500',
                'payment_mode': 'Cash',
                'note': '',
              },
              {
                'date': key.$4,
                'subcategory': 'Cardiology › Dr. Rao',
                'quantity': 6,
                'party': '',
                'amount': '900',
                'payment_mode': 'Cash',
                'note': '',
              },
              {
                'date': key.$4,
                'subcategory': '',
                'quantity': 0,
                'party': '',
                'amount': '100',
                'payment_mode': 'Cash',
                'note': '',
              },
            ],
          },
        ),
        ledgerProvider.overrideWith(
          (ref, key) async => {
            'title': 'Acme',
            'kind': key.$1,
            'balance': '700',
            'rows': [
              {
                'date': '2026-10-20',
                'particular': 'Bill received',
                'debit': '700',
                'credit': '0',
                'balance': '700',
              },
            ],
          },
        ),
        sessionControllerProvider.overrideWith(() => _FakeSession()),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: screen),
    ),
  );
  await tester.pumpAndSettle();
}

class _FakeSession extends SessionController {
  @override
  SessionState build() => const SessionState.signedIn(
    AppUser(
      email: 'a@b.c',
      name: 'Asha Rao',
      role: 'OWNER',
      organizationName: 'Rao Traders',
    ),
  );
}

void main() {
  for (final entry in <String, Widget>{
    'dashboard': const DashboardScreen(),
    'daily performance': const DailyPerformanceScreen(),
    'intelligence': const IntelligenceScreen(),
    'new entry': const EntryScreen(),
    'monthly summary': const MonthlySummaryScreen(),
    'billing': const BillingScreen(),
    'cash position': const CashPositionScreen(),
    'financial reports': const FinancialReportsScreen(),
    'bank accounts': const BankAccountsScreen(),
    'vendors': const VendorsScreen(),
    'aging': const AgingScreen(),
    'more': const MoreScreen(),
    'day detail': const DayDetailScreen(date: '2026-10-20'),
    'quantity detail': const QuantityDetailScreen(
      kind: 'sale',
      name: 'Walk-in',
      from: '2026-10-01',
      to: '2026-10-25',
    ),
    'category detail': const CategoryDetailScreen(
      kind: 'sale',
      name: 'Walk-in',
      from: '2026-10-01',
      to: '2026-10-25',
    ),
    'bank ledger': const LedgerScreen(
      kind: 'bank',
      id: '1',
      title: 'Current A/c',
    ),
    'vendor ledger': const LedgerScreen(kind: 'vendor', id: '1', title: 'Acme'),
  }.entries) {
    testWidgets('${entry.key} renders without layout errors', (tester) async {
      await _pump(tester, entry.value);
      expect(tester.takeException(), isNull);
      expect(find.byType(ErrorWidget), findsNothing);
    });
  }

  testWidgets('intelligence revenue tab shows quantity without layout errors', (
    tester,
  ) async {
    await _pump(tester, const IntelligenceScreen());
    await tester.tap(find.text('Revenue').first);
    await tester.pumpAndSettle();
    expect(find.text('Quantity by category'), findsOneWidget);
    expect(find.text('Quantity details'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('pulling down shows the Finday mark and refreshes', (
    tester,
  ) async {
    await _pump(tester, const VendorsScreen());
    await tester.pumpAndSettle();
    await tester.fling(find.byType(ListView).first, const Offset(0, 400), 1000);
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
