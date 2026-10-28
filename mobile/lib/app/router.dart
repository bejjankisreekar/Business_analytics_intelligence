import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/daily/presentation/category_detail_screen.dart';
import '../features/daily/presentation/category_statement_screen.dart';
import '../features/daily/presentation/quantity_detail_screen.dart';
import '../features/daily/presentation/daily_performance_screen.dart';
import '../features/daily/presentation/day_detail_screen.dart';
import '../features/more/presentation/ledger_screen.dart';
import '../features/intelligence/presentation/intelligence_screen.dart';
import '../features/more/presentation/aging_screen.dart';
import '../features/more/presentation/bank_accounts_screen.dart';
import '../features/more/presentation/cash_position_screen.dart';
import '../features/more/presentation/financial_reports_screen.dart';
import '../features/more/presentation/monthly_summary_screen.dart';
import '../features/more/presentation/more_screen.dart';
import '../features/more/presentation/billing_screen.dart';
import '../features/more/presentation/vendors_screen.dart';
import '../features/auth/application/session_controller.dart';
import '../features/auth/presentation/login_screen.dart';
import '../features/dashboard/presentation/dashboard_screen.dart';
import '../features/entries/presentation/entry_screen.dart';
import '../features/history/presentation/history_screen.dart';
import '../features/requests/presentation/request_detail_screen.dart';
import '../features/requests/presentation/requests_screen.dart';
import '../features/splash/presentation/splash_screen.dart';
import '../shared/widgets/main_shell.dart';

abstract final class AppRoutes {
  static const splash = '/splash';
  static const login = '/login';
  static const dashboard = '/dashboard';
  static const daily = '/daily';
  static const intelligence = '/intelligence';
  static const requests = '/requests';
  static const request = '/request';
  static const more = '/more';
  static const dailyBook = '/daily-book';
  static const monthlySummary = '/monthly-summary';
  static const cashPosition = '/cash-position';
  static const entries = '/entries';
  static const financialReports = '/financial-reports';
  static const bankAccounts = '/bank-accounts';
  static const vendors = '/vendors';
  static const billing = '/billing';
  static const aging = '/aging';
  static const day = '/day';
  static const category = '/category';
  static const quantity = '/quantity';
  static const categoryStatement = '/category-statement';
  static const ledger = '/ledger';
  static const dailyRange = '/daily-range';
}

final routerProvider = Provider<GoRouter>((ref) {
  // GoRouter re-runs `redirect` whenever this notifies, so auth changes take effect at once.
  final refresh = ValueNotifier<int>(0);
  ref.listen<SessionState>(sessionControllerProvider, (previous, next) {
    refresh.value++;
  });

  final router = GoRouter(
    initialLocation: AppRoutes.splash,
    refreshListenable: refresh,
    redirect: (context, state) =>
        _redirect(ref.read(sessionControllerProvider), state),
    routes: [
      GoRoute(path: AppRoutes.splash, builder: (_, _) => const SplashScreen()),
      GoRoute(path: AppRoutes.login, builder: (_, _) => const LoginScreen()),
      StatefulShellRoute.indexedStack(
        builder: (_, _, shell) => MainShell(shell: shell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.dashboard,
                builder: (_, _) => const DashboardScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.daily,
                builder: (_, _) => const DailyPerformanceScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.intelligence,
                builder: (_, _) => const IntelligenceScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.requests,
                builder: (_, _) => const RequestsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: AppRoutes.more,
                builder: (_, _) => const MoreScreen(),
              ),
            ],
          ),
        ],
      ),
      GoRoute(
        path: '${AppRoutes.request}/:number',
        builder: (_, state) => RequestDetailScreen(
          number: int.tryParse(state.pathParameters['number'] ?? '') ?? 0,
        ),
      ),
      GoRoute(
        path: AppRoutes.dailyBook,
        builder: (_, _) => const HistoryScreen(),
      ),
      GoRoute(
        path: AppRoutes.monthlySummary,
        builder: (_, _) => const MonthlySummaryScreen(),
      ),
      GoRoute(
        path: AppRoutes.cashPosition,
        builder: (_, _) => const CashPositionScreen(),
      ),
      GoRoute(path: AppRoutes.entries, builder: (_, _) => const EntryScreen()),
      GoRoute(
        path: AppRoutes.financialReports,
        builder: (_, _) => const FinancialReportsScreen(),
      ),
      GoRoute(
        path: AppRoutes.bankAccounts,
        builder: (_, _) => const BankAccountsScreen(),
      ),
      GoRoute(
        path: AppRoutes.billing,
        builder: (_, _) => const BillingScreen(),
      ),
      GoRoute(
        path: AppRoutes.vendors,
        builder: (_, _) => const VendorsScreen(),
      ),
      GoRoute(path: AppRoutes.aging, builder: (_, _) => const AgingScreen()),
      GoRoute(
        path: AppRoutes.day,
        builder: (_, state) =>
            DayDetailScreen(date: state.uri.queryParameters['date'] ?? ''),
      ),
      GoRoute(
        path: AppRoutes.categoryStatement,
        builder: (_, _) => const CategoryStatementScreen(),
      ),
      GoRoute(
        path: AppRoutes.quantity,
        builder: (_, state) {
          final q = state.uri.queryParameters;
          return QuantityDetailScreen(
            kind: q['kind'] ?? 'sale',
            name: q['name'] ?? '',
            from: q['from'] ?? '',
            to: q['to'] ?? '',
          );
        },
      ),
      GoRoute(
        path: AppRoutes.category,
        builder: (_, state) {
          final q = state.uri.queryParameters;
          return CategoryDetailScreen(
            kind: q['kind'] ?? 'sale',
            name: q['name'] ?? '',
            from: q['from'] ?? '',
            to: q['to'] ?? '',
          );
        },
      ),
      GoRoute(
        path: AppRoutes.ledger,
        builder: (_, state) => LedgerScreen(
          kind: state.uri.queryParameters['kind'] ?? 'bank',
          id: state.uri.queryParameters['id'] ?? '',
          title: state.uri.queryParameters['title'] ?? 'Ledger',
        ),
      ),
      GoRoute(
        path: AppRoutes.dailyRange,
        builder: (_, state) => DailyPerformanceScreen(
          initialFrom: state.uri.queryParameters['from'],
          initialTo: state.uri.queryParameters['to'],
        ),
      ),
    ],
  );

  ref.onDispose(() {
    router.dispose();
    refresh.dispose();
  });
  return router;
});

String? _redirect(SessionState session, GoRouterState state) {
  final location = state.matchedLocation;

  switch (session.status) {
    case SessionStatus.unknown:
      return location == AppRoutes.splash ? null : AppRoutes.splash;
    case SessionStatus.signedOut:
      return location == AppRoutes.login ? null : AppRoutes.login;
    case SessionStatus.signedIn:
      const authOnly = [AppRoutes.splash, AppRoutes.login];
      if (authOnly.contains(location)) return AppRoutes.dashboard;
      // Customer requests are an add-on: accounts without it never reach its screens.
      final isRequests =
          location == AppRoutes.requests ||
          location.startsWith('${AppRoutes.request}/');
      if (isRequests && !(session.user?.customerRequests ?? false)) {
        return AppRoutes.dashboard;
      }
      return null;
  }
}

/// Location helpers, so callers never hand-build query strings.
String dayLocation(String isoDate) =>
    Uri(path: AppRoutes.day, queryParameters: {'date': isoDate}).toString();

String categoryLocation({
  required String kind,
  required String name,
  required String from,
  required String to,
}) => Uri(
  path: AppRoutes.category,
  queryParameters: {'kind': kind, 'name': name, 'from': from, 'to': to},
).toString();

String ledgerLocation({
  required String kind,
  required String id,
  required String title,
}) => Uri(
  path: AppRoutes.ledger,
  queryParameters: {'kind': kind, 'id': id, 'title': title},
).toString();

String dailyRangeLocation({required String from, required String to}) => Uri(
  path: AppRoutes.dailyRange,
  queryParameters: {'from': from, 'to': to},
).toString();

String quantityLocation({
  required String kind,
  required String name,
  required String from,
  required String to,
}) => Uri(
  path: AppRoutes.quantity,
  queryParameters: {'kind': kind, 'name': name, 'from': from, 'to': to},
).toString();
