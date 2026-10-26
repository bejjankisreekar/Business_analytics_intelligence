import 'package:flutter/material.dart';

import '../../app/theme/app_colors.dart';
import '../../features/entries/models/entry_type.dart';
import 'report_widgets.dart';

IconData entryIcon(EntryType type) => switch (type) {
  EntryType.sale => Icons.south_west,
  EntryType.expense => Icons.north_east,
  EntryType.purchase => Icons.shopping_bag_outlined,
};

/// One entry in a detail list: icon, title, a muted detail line and a signed amount.
class EntryLineTile extends StatelessWidget {
  const EntryLineTile({
    super.key,
    required this.type,
    required this.title,
    required this.details,
    required this.amount,
    this.onTap,
  });

  final EntryType type;
  final String title;

  /// Muted second line parts; empty ones are skipped.
  final List<String> details;
  final num amount;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final line = details.where((d) => d.trim().isNotEmpty).join(' · ');
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Row(
          children: [
            IconBadge(icon: entryIcon(type), color: type.color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  if (line.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      line,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              '${type.sign > 0 ? '+' : '−'}${rupees(amount)}',
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w800,
                color: type.color,
              ),
            ),
            if (onTap != null) const TapChevron(),
          ],
        ),
      ),
    );
  }
}
