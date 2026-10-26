import 'package:finday_mobile/core/utils/date_formatter.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('isoDate pads month and day', () {
    expect(isoDate(DateTime(2026, 3, 7)), '2026-03-07');
  });

  test('displayDate is day, short month, year', () {
    expect(displayDate(DateTime(2026, 10, 24)), '24 Oct 2026');
  });

  test('formatAmount uses Indian grouping and drops .00', () {
    expect(formatAmount(1200), '1,200');
    expect(formatAmount(450.5), '450.50');
    expect(formatAmount(1234567), '12,34,567');
  });
}
