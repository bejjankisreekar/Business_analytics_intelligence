import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../app/theme/app_colors.dart';
import '../../../../core/network/api_exception.dart';
import '../../../../core/utils/date_formatter.dart';
import '../../../../shared/widgets/error_view.dart';
import '../../application/entries_providers.dart';
import '../../../../shared/widgets/report_widgets.dart';
import '../../models/entry_record.dart';
import '../../models/entry_type.dart';

/// Everything recorded on one day, with the net total underneath.
class DayEntriesList extends ConsumerWidget {
  const DayEntriesList({super.key, required this.date});

  /// ISO date, e.g. `2026-10-24`.
  final String date;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final entries = ref.watch(dayEntriesProvider(date));

    return entries.when(
      loading: () => const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => ErrorView(
        message: userMessage(error),
        onRetry: () => ref.invalidate(dayEntriesProvider(date)),
      ),
      data: (list) {
        if (list.isEmpty) {
          return const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text(
              'Nothing recorded for this day yet.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          );
        }
        final net = list.fold<num>(0, (sum, e) => sum + e.type.sign * e.amount);
        return Column(
          children: [
            for (final entry in list) _EntryTile(entry: entry),
            Align(
              alignment: Alignment.centerRight,
              child: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Net for the day: ${net < 0 ? '−' : ''}${formatAmount(net.abs())}',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _EntryTile extends StatelessWidget {
  const _EntryTile({required this.entry});

  final EntryRecord entry;

  @override
  Widget build(BuildContext context) {
    final detail = [
      entry.paymentMode,
      if (entry.note.isNotEmpty) entry.note,
    ].join(' · ');
    final icon = switch (entry.type) {
      EntryType.sale => Icons.south_west,
      EntryType.expense => Icons.north_east,
      EntryType.purchase => Icons.shopping_bag_outlined,
    };
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(16),
        boxShadow: AppColors.cardShadow,
      ),
      child: Row(
        children: [
          IconBadge(icon: icon, color: entry.type.color),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  entry.title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  detail,
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          Text(
            '${entry.type.sign > 0 ? '+' : '−'}₹${formatAmount(entry.amount)}',
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w800,
              color: entry.type.color,
            ),
          ),
        ],
      ),
    );
  }
}
