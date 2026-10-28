import 'package:finday_mobile/app/theme/app_theme.dart';
import 'package:finday_mobile/features/auth/models/app_user.dart';
import 'package:finday_mobile/features/requests/application/requests_providers.dart';
import 'package:finday_mobile/features/requests/presentation/request_detail_screen.dart';
import 'package:finday_mobile/features/requests/presentation/requests_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _row(
  int number,
  String name, {
  String status = 'PENDING',
  String payment = 'PENDING',
  String amount = '0.00',
  bool edited = false,
}) => {
  'number': number,
  'customer_name': name,
  'customer_phone': '98765 4321$number',
  'customer_address': '',
  'status': status,
  'payment_status': payment,
  'item_count': 2,
  'amount': amount,
  'created_at': '2026-10-28T10:15:00+05:30',
  'edited': edited,
};

Map<String, dynamic> _list(List<Map<String, dynamic>> rows) => {
  'day': {'key': 'today', 'label': 'Today, Wed, 28 Oct 2026', 'date': '2026-10-28'},
  'filters': {'status': '', 'payment': '', 'q': ''},
  'currency': 'INR',
  'counts': {'total': 3, 'pending': 1, 'quoted': 2, 'paid': 1, 'unpaid': 1},
  'amounts': {'total': '605.00', 'paid': '405.00', 'unpaid': '200.00'},
  'waiting_total': 1,
  'requests': rows,
};

Map<String, dynamic> _detail({
  String status = 'PENDING',
  String payment = 'PENDING',
  String? price,
}) => {
  'currency': 'INR',
  'request': {
    'number': 7,
    'customer_name': 'Ravi Kumar',
    'customer_phone': '98765 43210',
    'customer_address': '12 Gandhi Road',
    'status': status,
    'payment_status': payment,
    'payment_mode': payment == 'PAID' ? 'BANK' : '',
    'paid_at': payment == 'PAID' ? '2026-10-28T11:00:00+05:30' : null,
    'created_at': '2026-10-28T10:15:00+05:30',
    'edited_at': null,
    'version': 'v1',
    'total': '180.00',
    'customer_link': 'https://findayintelligence.com/r/abc/',
    'pdf_link': status == 'QUOTED' ? 'https://findayintelligence.com/r/abc/quote.pdf' : null,
  },
  'items': [
    {
      'id': 1,
      'serial': 1,
      'product_name': 'Colgate paste',
      'qty': '2',
      'unit': 'pc',
      'unit_label': 'pc',
      'available': true,
      'price': price,
      'remark': '',
      'line_total': null,
    },
    {
      'id': 2,
      'serial': 2,
      'product_name': 'Sugar',
      'qty': '5',
      'unit': 'kg',
      'unit_label': 'kg',
      'available': true,
      'price': null,
      'remark': '',
      'line_total': null,
    },
  ],
};

Future<ProviderContainer> _pump(
  WidgetTester tester,
  Widget screen, {
  Map<String, dynamic>? list,
  Map<String, dynamic>? detail,
}) async {
  tester.view.physicalSize = const Size(360 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        requestsListProvider.overrideWith((ref) async => list ?? _list([])),
        requestDetailProvider.overrideWith(
          (ref, number) async => detail ?? _detail(),
        ),
        waitingCountProvider.overrideWith((ref) async => 1),
      ],
      child: MaterialApp(theme: AppTheme.light(), home: screen),
    ),
  );
  await tester.pumpAndSettle();
  return ProviderScope.containerOf(tester.element(find.byType(Scaffold).first));
}

void main() {
  group('Requests tab', () {
    testWidgets('shows the day buttons, filters and each request', (tester) async {
      await _pump(
        tester,
        const RequestsScreen(),
        list: _list([
          _row(1, 'Sreekar', status: 'QUOTED', payment: 'PAID', amount: '405.00'),
          _row(2, 'Ravi Kumar', status: 'QUOTED', amount: '200.00'),
          _row(3, 'Meena', edited: true),
        ]),
      );
      expect(tester.takeException(), isNull);
      for (final label in ['Today', 'Yesterday', 'Pick a date', 'All days']) {
        expect(find.text(label), findsOneWidget);
      }
      expect(find.text('Showing: Today, Wed, 28 Oct 2026'), findsOneWidget);
      expect(find.text('All 3'), findsOneWidget);
      expect(find.text('Waiting 1'), findsOneWidget);
      expect(find.text('Unpaid 1'), findsOneWidget);
      expect(find.text('Sreekar'), findsOneWidget);
      expect(find.text('₹405'), findsWidgets);
      expect(find.text('Paid'), findsWidgets);
      expect(find.text('Waiting for quote'), findsOneWidget);
      expect(find.text('edited'), findsOneWidget);
    });

    testWidgets('says so when a day has no requests', (tester) async {
      await _pump(tester, const RequestsScreen());
      expect(
        find.textContaining('Nothing here for this day'),
        findsOneWidget,
      );
    });

    testWidgets('the day buttons and status chips change the filter', (tester) async {
      final container = await _pump(
        tester,
        const RequestsScreen(),
        list: _list([_row(1, 'Sreekar')]),
      );
      expect(container.read(requestsFilterProvider).day, '');

      await tester.tap(find.text('Yesterday'));
      await tester.pumpAndSettle();
      expect(container.read(requestsFilterProvider).day, 'yesterday');

      await tester.tap(find.text('All days'));
      await tester.pumpAndSettle();
      expect(container.read(requestsFilterProvider).day, 'all');

      await tester.tap(find.text('Unpaid 1'));
      await tester.pumpAndSettle();
      var filter = container.read(requestsFilterProvider);
      expect((filter.payment, filter.status), ('UNPAID', ''));

      await tester.tap(find.text('Waiting 1'));
      await tester.pumpAndSettle();
      filter = container.read(requestsFilterProvider);
      expect((filter.status, filter.payment), ('PENDING', ''));   // one choice at a time
    });

    testWidgets('searching sets the query', (tester) async {
      final container = await _pump(tester, const RequestsScreen());
      await tester.enterText(find.byType(TextField), 'meena');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(container.read(requestsFilterProvider).query, 'meena');
    });
  });

  group('Request detail', () {
    testWidgets('lists the items and the quote total follows the rates', (tester) async {
      await _pump(tester, const RequestDetailScreen(number: 7));
      expect(tester.takeException(), isNull);
      expect(find.text('Ravi Kumar'), findsOneWidget);
      expect(find.text('12 Gandhi Road'), findsOneWidget);
      expect(find.text('Colgate paste'), findsOneWidget);
      expect(find.text('2 pc'), findsOneWidget);
      expect(find.text('Send quote'), findsOneWidget);
      expect(find.text('Mark as paid'), findsNothing);          // not until the quote is sent

      await tester.enterText(find.widgetWithText(TextField, 'Rate per pc'), '90');
      await tester.enterText(find.widgetWithText(TextField, 'Rate per kg'), '45');
      await tester.pump();
      expect(find.text('₹90 x 2 = ₹180'), findsOneWidget);
      expect(find.text('₹45 x 5 = ₹225'), findsOneWidget);
      expect(find.text('₹405'), findsOneWidget);                // the quote total
    });

    testWidgets('turning an item off removes it from the total', (tester) async {
      await _pump(tester, const RequestDetailScreen(number: 7));
      await tester.enterText(find.widgetWithText(TextField, 'Rate per pc'), '90');
      await tester.pump();
      expect(find.text('₹180'), findsOneWidget);

      await tester.tap(find.byType(Switch).first);
      await tester.pump();
      expect(find.text('₹90 x 2 = ₹180'), findsNothing);
      expect(find.text('Can supply'), findsOneWidget);
      expect(find.text('1 items'), findsWidgets);               // only Sugar is left to supply
    });

    testWidgets('sending without a rate is refused before anything is sent', (tester) async {
      await _pump(tester, const RequestDetailScreen(number: 7));
      await tester.tap(find.text('Send quote'));
      await tester.pump();
      expect(
        find.text('Enter a rate for "Colgate paste", or turn off Available.'),
        findsOneWidget,
      );
    });

    testWidgets('a quoted request offers payment, a paid one offers to undo it', (tester) async {
      await _pump(
        tester,
        const RequestDetailScreen(number: 7),
        detail: _detail(status: 'QUOTED', price: '90'),
      );
      expect(find.text('Update quote'), findsOneWidget);
      expect(find.text('Mark as paid'), findsOneWidget);
      expect(find.text('Cash'), findsOneWidget);
      expect(find.text('Online'), findsOneWidget);
      expect(find.text('Copy PDF link'), findsOneWidget);
    });

    testWidgets('a paid request shows how it was paid', (tester) async {
      await _pump(
        tester,
        const RequestDetailScreen(number: 7),
        detail: _detail(status: 'QUOTED', payment: 'PAID', price: '90'),
      );
      expect(find.text('Mark as pending'), findsOneWidget);
      expect(find.text('Mark as paid'), findsNothing);
      expect(find.textContaining('by online'), findsOneWidget);
    });
  });

  group('Who gets the tab', () {
    AppUser parse(Map<String, dynamic> extra) => AppUser.fromJson({
      'user': {'email': 'o@shop.example', 'name': 'Owner', 'role': 'OWNER'},
      'organization': {'name': 'Corner Shop'},
      ...extra,
    });

    test('the server flag turns the tab on', () {
      expect(parse({'features': {'customer_requests': true}}).customerRequests, isTrue);
    });

    test('off, or missing from an older server, means no tab', () {
      expect(parse({'features': {'customer_requests': false}}).customerRequests, isFalse);
      expect(parse({}).customerRequests, isFalse);
    });
  });
}
