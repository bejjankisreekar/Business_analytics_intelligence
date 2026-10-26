import 'package:flutter/material.dart';

class AppColors {
  const AppColors._();

  static const Color brand = Color(0xFF4F46E5);
  static const Color brandDark = Color(0xFF3730A3);
  static const Color brandSoft = Color(0xFFEEF0FF);
  static const Color accent = Color(0xFF7C3AED);
  static const Color onBrand = Color(0xFFFFFFFF);
  static const Color ink = Color(0xFF0F172A);
  static const Color surface = Color(0xFFF4F5FB);
  static const Color card = Color(0xFFFFFFFF);
  static const Color divider = Color(0xFFE8EAF2);
  static const Color textPrimary = Color(0xFF0F172A);
  static const Color textSecondary = Color(0xFF64748B);

  /// Entry-type accents: revenue, expense, purchase.
  static const Color sale = Color(0xFF059669);
  static const Color expense = Color(0xFFE11D48);
  static const Color purchase = Color(0xFFF59E0B);
  static const Color danger = Color(0xFFE11D48);

  static const LinearGradient heroGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF4338CA), Color(0xFF7C3AED)],
  );

  /// Soft elevation used by cards.
  static const List<BoxShadow> cardShadow = [
    BoxShadow(color: Color(0x141E1B4B), blurRadius: 24, offset: Offset(0, 8)),
    BoxShadow(color: Color(0x0A1E1B4B), blurRadius: 4, offset: Offset(0, 1)),
  ];

  /// Series colours for donut charts and legends.
  static const List<Color> palette = [
    Color(0xFF4F46E5),
    Color(0xFF0EA5E9),
    Color(0xFF10B981),
    Color(0xFFF59E0B),
    Color(0xFFE11D48),
    Color(0xFF8B5CF6),
    Color(0xFF64748B),
  ];
}
