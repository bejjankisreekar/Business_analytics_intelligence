/// `2026-10-24`, the format the API uses for dates.
String isoDate(DateTime date) =>
    '${date.year.toString().padLeft(4, '0')}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';

const _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/// `24 Oct 2026`, for showing a date to people.
String displayDate(DateTime date) =>
    '${date.day} ${_months[date.month - 1]} ${date.year}';

/// Indian digit grouping, e.g. `52,25,365` or `1,200.50`; whole numbers drop the trailing `.00`.
String formatAmount(num value) {
  final negative = value < 0;
  final fixed = value.abs() == value.abs().roundToDouble()
      ? value.abs().toStringAsFixed(0)
      : value.abs().toStringAsFixed(2);
  final parts = fixed.split('.');
  final digits = parts.first;
  final grouped = digits.length <= 3
      ? digits
      : '${digits.substring(0, digits.length - 3).replaceAllMapped(RegExp(r'\B(?=(\d{2})+(?!\d))'), (_) => ',')},'
            '${digits.substring(digits.length - 3)}';
  return '${negative ? '-' : ''}$grouped${parts.length > 1 ? '.${parts[1]}' : ''}';
}
