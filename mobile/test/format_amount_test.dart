import 'package:flutter_test/flutter_test.dart';
import 'package:finday_mobile/core/utils/date_formatter.dart';

void main() {
  test('formats amounts with Indian digit grouping', () {
    expect(formatAmount(5225365), '52,25,365');
    expect(formatAmount(999), '999');
    expect(formatAmount(1000), '1,000');
    expect(formatAmount(123456), '1,23,456');
    expect(formatAmount(12345678.5), '1,23,45,678.50');
    expect(formatAmount(-100000), '-1,00,000');
    expect(formatAmount(0), '0');
  });
}
