import 'package:flutter/material.dart';

import '../../app/theme/app_colors.dart';

/// Brand mark: a bold "F" whose arms are bars, with a small rising trend line to hint at analytics.
///
/// Set [onBrand] when drawing on a brand-coloured background (the splash screen) to get a white tile.
class FindayLogo extends StatelessWidget {
  const FindayLogo({super.key, this.size = 64, this.onBrand = false});

  final double size;
  final bool onBrand;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _LogoPainter(onBrand: onBrand)),
    );
  }
}

class _LogoPainter extends CustomPainter {
  const _LogoPainter({required this.onBrand});

  final bool onBrand;

  @override
  void paint(Canvas canvas, Size size) {
    final s = size.width;
    final tile = RRect.fromRectAndRadius(
      Offset.zero & size,
      Radius.circular(s * 0.28),
    );

    final tilePaint = Paint();
    if (onBrand) {
      tilePaint.color = Colors.white;
    } else {
      tilePaint.shader = const LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFF6366F1), AppColors.brandDark],
      ).createShader(Offset.zero & size);
    }
    canvas.drawRRect(tile, tilePaint);

    final ink = onBrand ? AppColors.brand : Colors.white;
    final bar = Paint()..color = ink;
    final r = Radius.circular(s * 0.06);

    // Stem and the two arms of the F.
    canvas.drawRRect(
      RRect.fromLTRBR(s * 0.27, s * 0.22, s * 0.40, s * 0.78, r),
      bar,
    );
    canvas.drawRRect(
      RRect.fromLTRBR(s * 0.27, s * 0.22, s * 0.74, s * 0.35, r),
      bar,
    );
    canvas.drawRRect(
      RRect.fromLTRBR(s * 0.27, s * 0.45, s * 0.62, s * 0.57, r),
      bar,
    );

    // Rising trend line with a dot, in the accent colour, to the right of the stem.
    final accent = onBrand ? AppColors.sale : const Color(0xFF34D399);
    final line = Paint()
      ..color = accent
      ..style = PaintingStyle.stroke
      ..strokeWidth = s * 0.055
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final path = Path()
      ..moveTo(s * 0.50, s * 0.76)
      ..lineTo(s * 0.62, s * 0.66)
      ..lineTo(s * 0.70, s * 0.71)
      ..lineTo(s * 0.80, s * 0.58);
    canvas.drawPath(path, line);
    canvas.drawCircle(
      Offset(s * 0.80, s * 0.58),
      s * 0.04,
      Paint()..color = accent,
    );
  }

  @override
  bool shouldRepaint(_LogoPainter old) => old.onBrand != onBrand;
}
