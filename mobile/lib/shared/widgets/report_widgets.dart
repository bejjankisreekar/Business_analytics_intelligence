import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme/app_colors.dart';
import '../../core/network/api_exception.dart';
import '../../core/utils/date_formatter.dart';
import 'error_view.dart';

/// Reads an API amount (sent as a string) as a number.
num parseNum(Object? value) => num.tryParse('$value') ?? 0;

String rupees(num value) =>
    '${value < 0 ? '-' : ''}₹${formatAmount(value.abs())}';

/// Compact form for tight spots: ₹1.2L, ₹3.4Cr.
String rupeesShort(num value) {
  final v = value.abs();
  final sign = value < 0 ? '-' : '';
  if (v >= 10000000) return '$sign₹${(v / 10000000).toStringAsFixed(1)}Cr';
  if (v >= 100000) return '$sign₹${(v / 100000).toStringAsFixed(1)}L';
  if (v >= 1000) return '$sign₹${(v / 1000).toStringAsFixed(1)}K';
  return '$sign₹${v.toStringAsFixed(0)}';
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 24, bottom: 12),
    child: Row(
      children: [
        Expanded(
          child: Text(
            text,
            style: const TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w800,
              letterSpacing: -0.2,
            ),
          ),
        ),
        ?trailing,
      ],
    ),
  );
}

class ReportCard extends StatelessWidget {
  const ReportCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(16),
    this.onTap,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;

  /// When set, the whole card is tappable and shows a ripple.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(20);
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: radius,
        boxShadow: AppColors.cardShadow,
      ),
      child: Material(
        color: Colors.transparent,
        borderRadius: radius,
        child: InkWell(
          borderRadius: radius,
          onTap: onTap,
          child: Padding(padding: padding, child: child),
        ),
      ),
    );
  }
}

/// A small chevron that hints a row opens something.
class TapChevron extends StatelessWidget {
  const TapChevron({super.key});

  @override
  Widget build(BuildContext context) => const Icon(
    Icons.chevron_right_rounded,
    size: 20,
    color: AppColors.textSecondary,
  );
}

/// A rounded icon on a soft tint of [color].
class IconBadge extends StatelessWidget {
  const IconBadge({
    super.key,
    required this.icon,
    required this.color,
    this.size = 40,
  });

  final IconData icon;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.12),
      borderRadius: BorderRadius.circular(size * 0.32),
    ),
    child: Icon(icon, color: color, size: size * 0.52),
  );
}

/// A small pill such as "▲ 12.5%".
class DeltaPill extends StatelessWidget {
  const DeltaPill({super.key, required this.pct, this.onDark = false});

  final Object? pct;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    if (pct == null) return const SizedBox.shrink();
    final value = parseNum(pct);
    final up = value >= 0;
    final color = onDark
        ? Colors.white
        : (up ? AppColors.sale : AppColors.expense);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: onDark
            ? Colors.white.withValues(alpha: 0.18)
            : color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            up ? Icons.trending_up : Icons.trending_down,
            size: 13,
            color: color,
          ),
          const SizedBox(width: 3),
          Text(
            '${value.abs()}%',
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w700,
              color: color,
            ),
          ),
        ],
      ),
    );
  }
}

/// A label over a big value, e.g. "Cash in hand / ₹12,000".
class StatTile extends StatelessWidget {
  const StatTile({
    super.key,
    required this.label,
    required this.value,
    this.color,
    this.hint,
    this.icon,
  });

  final String label;
  final String value;
  final Color? color;
  final String? hint;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      if (icon != null) ...[
        IconBadge(icon: icon!, color: color ?? AppColors.brand, size: 34),
        const SizedBox(height: 10),
      ],
      Text(
        label,
        style: const TextStyle(
          fontSize: 12,
          color: AppColors.textSecondary,
          fontWeight: FontWeight.w500,
        ),
      ),
      const SizedBox(height: 3),
      FittedBox(
        fit: BoxFit.scaleDown,
        alignment: Alignment.centerLeft,
        child: Text(
          value,
          style: TextStyle(
            fontSize: 19,
            fontWeight: FontWeight.w800,
            letterSpacing: -0.3,
            color: color ?? AppColors.textPrimary,
          ),
        ),
      ),
      if (hint case final hint?) ...[
        const SizedBox(height: 2),
        Text(
          hint,
          style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
        ),
      ],
    ],
  );
}

/// [StatTile]s laid out two per row, each on its own card.
class StatGrid extends StatelessWidget {
  const StatGrid({super.key, required this.tiles});

  final List<StatTile> tiles;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final width = (constraints.maxWidth - 12) / 2;
      return Wrap(
        spacing: 12,
        runSpacing: 12,
        children: [
          for (final tile in tiles)
            SizedBox(
              width: width,
              child: ReportCard(padding: const EdgeInsets.all(14), child: tile),
            ),
        ],
      );
    },
  );
}

/// The headline card: a gradient panel with the big number and a few supporting figures.
class HeroCard extends StatelessWidget {
  const HeroCard({
    super.key,
    required this.eyebrow,
    required this.value,
    required this.figures,
    this.delta,
    this.caption,
  });

  final String eyebrow;
  final String value;
  final Object? delta;
  final String? caption;
  final List<({String label, String value, IconData icon})> figures;

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(20),
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
    child: Stack(
      children: [
        Positioned(
          right: -30,
          top: -50,
          child: Container(
            width: 130,
            height: 130,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.white.withValues(alpha: 0.08),
            ),
          ),
        ),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    eyebrow,
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.8),
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                DeltaPill(pct: delta, onDark: true),
              ],
            ),
            const SizedBox(height: 6),
            FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.centerLeft,
              child: Text(
                value,
                style: const TextStyle(
                  fontSize: 36,
                  fontWeight: FontWeight.w800,
                  letterSpacing: -1,
                  color: Colors.white,
                ),
              ),
            ),
            if (caption != null)
              Text(
                caption!,
                style: TextStyle(
                  fontSize: 12,
                  color: Colors.white.withValues(alpha: 0.75),
                ),
              ),
            if (figures.isNotEmpty) const SizedBox(height: 18),
            if (figures.isNotEmpty)
              Container(
                padding: const EdgeInsets.symmetric(
                  vertical: 12,
                  horizontal: 12,
                ),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Row(
                  children: [
                    for (final f in figures)
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Icon(
                                  f.icon,
                                  size: 13,
                                  color: Colors.white.withValues(alpha: 0.8),
                                ),
                                const SizedBox(width: 4),
                                Flexible(
                                  child: Text(
                                    f.label,
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                      fontSize: 11,
                                      color: Colors.white.withValues(
                                        alpha: 0.8,
                                      ),
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 3),
                            FittedBox(
                              fit: BoxFit.scaleDown,
                              alignment: Alignment.centerLeft,
                              child: Text(
                                f.value,
                                style: const TextStyle(
                                  fontWeight: FontWeight.w800,
                                  color: Colors.white,
                                  fontSize: 15,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ],
    ),
  );
}

/// Ranked name/amount rows with a proportional bar, from an API list of `{name, amount}`.
class BreakdownBars extends StatelessWidget {
  const BreakdownBars({
    super.key,
    required this.items,
    required this.color,
    this.limit = 8,
    this.onItemTap,
    this.quantityMode = false,
  });

  /// Show each row's `quantity` as a count instead of its `amount` as money.
  final bool quantityMode;

  final List<dynamic> items;
  final Color color;
  final int limit;

  /// Called with the row's name when it is tapped; rows are not tappable when null.
  final ValueChanged<String>? onItemTap;

  @override
  Widget build(BuildContext context) {
    final valueKey = quantityMode ? 'quantity' : 'amount';
    final rows = items.take(limit).cast<Map<String, dynamic>>().toList();
    if (rows.isEmpty)
      return const _EmptyCard('Nothing recorded for this period.');
    final total = rows
        .map((r) => parseNum(r[valueKey]))
        .fold<num>(0, (a, b) => a + b);
    final peak = rows
        .map((r) => parseNum(r[valueKey]))
        .fold<num>(0, (a, b) => a > b ? a : b);
    return ReportCard(
      child: Column(
        children: [
          for (final row in rows)
            InkWell(
              borderRadius: BorderRadius.circular(10),
              onTap: onItemTap == null
                  ? null
                  : () => onItemTap!('${row['name']}'),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 7),
                child: Column(
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '${row['name']}',
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        if (!quantityMode && parseNum(row['quantity']) > 0) ...[
                          QuantityPill(parseNum(row['quantity'])),
                          const SizedBox(width: 8),
                        ],
                        Flexible(
                          child: FittedBox(
                            fit: BoxFit.scaleDown,
                            child: Text(
                              quantityMode
                                  ? '${parseNum(row['quantity']).round()} qty'
                                  : rupees(parseNum(row['amount'])),
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ),
                        ),
                        if (total > 0) ...[
                          const SizedBox(width: 8),
                          SizedBox(
                            width: 38,
                            child: Text(
                              '${(parseNum(row[valueKey]) / total * 100).round()}%',
                              textAlign: TextAlign.right,
                              style: const TextStyle(
                                fontSize: 12,
                                color: AppColors.textSecondary,
                              ),
                            ),
                          ),
                        ],
                        if (onItemTap != null) const TapChevron(),
                      ],
                    ),
                    const SizedBox(height: 6),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: LinearProgressIndicator(
                        value: peak <= 0
                            ? 0
                            : (parseNum(row[valueKey]) / peak)
                                  .clamp(0, 1)
                                  .toDouble(),
                        minHeight: 8,
                        color: color,
                        backgroundColor: color.withValues(alpha: 0.12),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _EmptyCard extends StatelessWidget {
  const _EmptyCard(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => ReportCard(
    child: Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          text,
          style: const TextStyle(color: AppColors.textSecondary),
        ),
      ),
    ),
  );
}

/// A donut with a legend, from an API list of `{name, amount}`.
class DonutBreakdown extends StatelessWidget {
  const DonutBreakdown({
    super.key,
    required this.items,
    required this.centerLabel,
    this.onItemTap,
  });

  final List<dynamic> items;
  final String centerLabel;

  /// Called with the slice's name when its legend row is tapped ("Others" is not tappable).
  final ValueChanged<String>? onItemTap;

  @override
  Widget build(BuildContext context) {
    final all = items.cast<Map<String, dynamic>>();
    if (all.isEmpty)
      return const _EmptyCard('Nothing recorded for this period.');
    // Keep the chart readable: top 5 and an "Others" slice.
    final shown = all.take(5).toList();
    final rest = all.skip(5).fold<num>(0, (a, r) => a + parseNum(r['amount']));
    final slices = [
      for (final r in shown)
        (name: '${r['name']}', amount: parseNum(r['amount'])),
      if (rest > 0) (name: 'Others', amount: rest),
    ];
    final total = slices.fold<num>(0, (a, s) => a + s.amount);

    return ReportCard(
      child: Row(
        children: [
          SizedBox(
            width: 124,
            height: 124,
            child: CustomPaint(
              painter: _DonutPainter([
                for (final s in slices) s.amount.toDouble(),
              ]),
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      centerLabel,
                      style: const TextStyle(
                        fontSize: 10,
                        color: AppColors.textSecondary,
                      ),
                    ),
                    Text(
                      rupeesShort(total),
                      style: const TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: 15,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              children: [
                for (var i = 0; i < slices.length; i++)
                  InkWell(
                    borderRadius: BorderRadius.circular(8),
                    onTap: onItemTap == null || slices[i].name == 'Others'
                        ? null
                        : () => onItemTap!(slices[i].name),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        children: [
                          Container(
                            width: 10,
                            height: 10,
                            decoration: BoxDecoration(
                              color: AppColors
                                  .palette[i % AppColors.palette.length],
                              borderRadius: BorderRadius.circular(3),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              slices[i].name,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 13),
                            ),
                          ),
                          Text(
                            '${total == 0 ? 0 : (slices[i].amount / total * 100).round()}%',
                            style: const TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _DonutPainter extends CustomPainter {
  _DonutPainter(this.values);

  final List<double> values;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 16.0;
    final rect = Rect.fromLTWH(
      stroke / 2,
      stroke / 2,
      size.width - stroke,
      size.height - stroke,
    );
    final total = values.fold<double>(0, (a, b) => a + b);
    if (total <= 0) return;
    var start = -math.pi / 2;
    for (var i = 0; i < values.length; i++) {
      final sweep = values[i] / total * 2 * math.pi;
      final gap = values.length > 1 ? 0.05 : 0.0;
      canvas.drawArc(
        rect,
        start + gap / 2,
        math.max(sweep - gap, 0.001),
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = stroke
          ..strokeCap = StrokeCap.round
          ..color = AppColors.palette[i % AppColors.palette.length],
      );
      start += sweep;
    }
  }

  @override
  bool shouldRepaint(_DonutPainter old) => old.values != values;
}

/// Smooth area/line chart of revenue against expense + purchase over a series.
class LineTrend extends StatelessWidget {
  const LineTrend({
    super.key,
    required this.points,
    required this.labelOf,
    this.height = 160,
  });

  final List<Map<String, dynamic>> points;
  final String Function(Map<String, dynamic> point) labelOf;
  final double height;

  @override
  Widget build(BuildContext context) {
    final sales = [for (final p in points) parseNum(p['sale']).toDouble()];
    final out = [
      for (final p in points)
        (parseNum(p['expense']) + parseNum(p['purchase'])).toDouble(),
    ];
    final peak = [...sales, ...out].fold<double>(0, math.max);

    return ReportCard(
      child: Column(
        children: [
          Row(
            children: [
              const Flexible(
                child: _Legend(color: AppColors.sale, label: 'Revenue'),
              ),
              const SizedBox(width: 12),
              const Flexible(
                flex: 2,
                child: _Legend(
                  color: AppColors.expense,
                  label: 'Expense + purchase',
                ),
              ),
              const Spacer(),
              Text(
                'Peak ${rupeesShort(peak)}',
                style: const TextStyle(
                  fontSize: 11,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: height,
            width: double.infinity,
            child: CustomPaint(painter: _LinePainter(sales, out, peak)),
          ),
          const SizedBox(height: 6),
          if (points.length > 1)
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  labelOf(points.first),
                  style: const TextStyle(
                    fontSize: 10,
                    color: AppColors.textSecondary,
                  ),
                ),
                if (points.length > 2)
                  Text(
                    labelOf(points[points.length ~/ 2]),
                    style: const TextStyle(
                      fontSize: 10,
                      color: AppColors.textSecondary,
                    ),
                  ),
                Text(
                  labelOf(points.last),
                  style: const TextStyle(
                    fontSize: 10,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter(this.sales, this.out, this.peak);

  final List<double> sales;
  final List<double> out;
  final double peak;

  Path _smooth(List<double> values, Size size) {
    final path = Path();
    if (values.isEmpty) return path;
    final step = values.length == 1 ? 0.0 : size.width / (values.length - 1);
    Offset at(int i) => Offset(
      i * step,
      size.height - (peak <= 0 ? 0 : values[i] / peak * (size.height - 8)) - 4,
    );
    path.moveTo(at(0).dx, at(0).dy);
    for (var i = 1; i < values.length; i++) {
      final a = at(i - 1);
      final b = at(i);
      final mid = (a.dx + b.dx) / 2;
      path.cubicTo(mid, a.dy, mid, b.dy, b.dx, b.dy);
    }
    return path;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final grid = Paint()
      ..color = AppColors.divider
      ..strokeWidth = 1;
    for (var i = 0; i < 4; i++) {
      final y = size.height * i / 3;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }

    final salesPath = _smooth(sales, size);
    final fill = Path.from(salesPath)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            AppColors.sale.withValues(alpha: 0.25),
            AppColors.sale.withValues(alpha: 0.0),
          ],
        ).createShader(Offset.zero & size),
    );

    Paint line(Color c) => Paint()
      ..color = c
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round;
    canvas.drawPath(_smooth(out, size), line(AppColors.expense));
    canvas.drawPath(salesPath, line(AppColors.sale));
  }

  @override
  bool shouldRepaint(_LinePainter old) =>
      old.sales != sales || old.out != out || old.peak != peak;
}

class _Legend extends StatelessWidget {
  const _Legend({required this.color, required this.label});

  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      ),
      const SizedBox(width: 5),
      Flexible(
        child: Text(
          label,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            fontSize: 11,
            color: AppColors.textSecondary,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    ],
  );
}

/// Paired revenue / outgoing bars over a series of `{sale, expense, purchase}` points.
class TrendBars extends StatelessWidget {
  const TrendBars({
    super.key,
    required this.points,
    required this.labelOf,
    this.barHeight = 110,
    this.onPointTap,
  });

  final ValueChanged<Map<String, dynamic>>? onPointTap;
  final List<Map<String, dynamic>> points;
  final String Function(Map<String, dynamic> point) labelOf;
  final double barHeight;

  @override
  Widget build(BuildContext context) {
    num outgoing(Map<String, dynamic> p) =>
        parseNum(p['expense']) + parseNum(p['purchase']);
    var peak = 0.0;
    for (final p in points) {
      peak = [
        peak,
        parseNum(p['sale']).toDouble(),
        outgoing(p).toDouble(),
      ].reduce((a, b) => a > b ? a : b);
    }
    double height(num value) => peak == 0 || value <= 0
        ? 0
        : (value / peak * barHeight).clamp(4.0, barHeight).toDouble();

    return ReportCard(
      child: Column(
        children: [
          const Row(
            children: [
              Flexible(
                child: _Legend(color: AppColors.sale, label: 'Revenue'),
              ),
              SizedBox(width: 16),
              Flexible(
                flex: 2,
                child: _Legend(
                  color: AppColors.expense,
                  label: 'Expense + purchase',
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              for (final p in points)
                Expanded(
                  child: InkWell(
                    borderRadius: BorderRadius.circular(8),
                    onTap: onPointTap == null ? null : () => onPointTap!(p),
                    child: Column(
                      children: [
                        SizedBox(
                          height: barHeight,
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            crossAxisAlignment: CrossAxisAlignment.end,
                            children: [
                              _Bar(
                                height: height(parseNum(p['sale'])),
                                color: AppColors.sale,
                              ),
                              const SizedBox(width: 2),
                              _Bar(
                                height: height(outgoing(p)),
                                color: AppColors.expense,
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          labelOf(p),
                          style: const TextStyle(
                            fontSize: 10,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Bar extends StatelessWidget {
  const _Bar({required this.height, required this.color});

  final double height;
  final Color color;

  @override
  Widget build(BuildContext context) => Flexible(
    child: Container(
      constraints: const BoxConstraints(maxWidth: 12),
      height: height,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [color, color.withValues(alpha: 0.55)],
        ),
        borderRadius: const BorderRadius.vertical(top: Radius.circular(6)),
      ),
    ),
  );
}

/// Revenue / expense / purchase / net for one row of a list (a day or a month).
class TotalsRow extends StatelessWidget {
  const TotalsRow({
    super.key,
    required this.title,
    required this.data,
    this.onTap,
  });

  final String title;
  final VoidCallback? onTap;
  final Map<String, dynamic> data;

  @override
  Widget build(BuildContext context) {
    final net = parseNum(data['net']);
    final positive = net >= 0;
    Widget figure(String label, String key, Color color) => Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 6,
                height: 6,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 5),
              Flexible(
                child: Text(
                  label,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 11,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 3),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(
              rupees(parseNum(data[key])),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: ReportCard(
        padding: const EdgeInsets.all(14),
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    title,
                    style: const TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 15,
                    ),
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 4,
                  ),
                  decoration: BoxDecoration(
                    color: (positive ? AppColors.sale : AppColors.expense)
                        .withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    'Net ${rupees(net)}',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                      color: positive ? AppColors.sale : AppColors.expense,
                    ),
                  ),
                ),
                if (onTap != null) const TapChevron(),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                figure('Revenue', 'sale', AppColors.sale),
                figure('Expense', 'expense', AppColors.expense),
                figure('Purchase', 'purchase', AppColors.purchase),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Loading / error / data handling shared by the report pages.
Widget asyncBody<T>(
  AsyncValue<T> value, {
  required VoidCallback onRetry,
  required Widget Function(T data) builder,
}) {
  return value.when(
    loading: () => const Padding(
      padding: EdgeInsets.symmetric(vertical: 80),
      child: Center(child: CircularProgressIndicator()),
    ),
    error: (error, _) =>
        ErrorView(message: userMessage(error), onRetry: onRetry),
    data: builder,
  );
}

/// One label/value line inside a [ReportCard]; `bold` for subtotals and totals.
class LineRow extends StatelessWidget {
  const LineRow(
    this.label,
    this.value, {
    super.key,
    this.bold = false,
    this.color,
    this.onTap,
  });

  final VoidCallback? onTap;
  final String label;
  final String value;
  final bool bold;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final weight = bold ? FontWeight.w800 : FontWeight.w500;
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            Expanded(
              child: Text(label, style: TextStyle(fontWeight: weight)),
            ),
            Text(
              value,
              style: TextStyle(fontWeight: weight, color: color),
            ),
            if (onTap != null) const TapChevron(),
          ],
        ),
      ),
    );
  }
}

/// "12 qty" chip shown beside revenue and purchase amounts.
class QuantityPill extends StatelessWidget {
  const QuantityPill(this.quantity, {super.key});

  final num quantity;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
    decoration: BoxDecoration(
      color: AppColors.brandSoft,
      borderRadius: BorderRadius.circular(20),
    ),
    child: Text(
      'Qty ${quantity.round()}',
      style: const TextStyle(
        fontSize: 11,
        fontWeight: FontWeight.w700,
        color: AppColors.brand,
      ),
    ),
  );
}

/// Labelled dropdown for choosing a period (or any single option), styled as a card.
class DropdownField extends StatelessWidget {
  const DropdownField({
    super.key,
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.icon,
  });

  final String label;
  final String? value;

  /// value -> label.
  final Map<String, String> items;
  final ValueChanged<String?>? onChanged;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => InputDecorator(
    decoration: InputDecoration(
      labelText: label,
      prefixIcon: icon == null ? null : Icon(icon, size: 20),
      filled: true,
      fillColor: AppColors.card,
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AppColors.divider),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AppColors.divider),
      ),
    ),
    child: DropdownButtonHideUnderline(
      child: DropdownButton<String>(
        isExpanded: true,
        value: items.containsKey(value) ? value : null,
        borderRadius: BorderRadius.circular(14),
        style: const TextStyle(
          fontWeight: FontWeight.w600,
          color: AppColors.textPrimary,
          fontSize: 15,
        ),
        items: [
          for (final e in items.entries)
            DropdownMenuItem(
              value: e.key,
              child: Text(e.value, overflow: TextOverflow.ellipsis),
            ),
        ],
        onChanged: onChanged,
      ),
    ),
  );
}
