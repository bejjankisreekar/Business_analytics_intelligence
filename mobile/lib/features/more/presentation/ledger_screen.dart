import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/widgets/branded_refresh.dart';
import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Running-balance ledger of a bank account or a vendor, newest first.
class LedgerScreen extends ConsumerWidget {
  const LedgerScreen({
    super.key,
    required this.kind,
    required this.id,
    required this.title,
  });

  /// `bank` or `vendor`.
  final String kind;
  final String id;
  final String title;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final key = (kind, id);
    final isBank = kind == 'bank';
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: BrandedRefresh(
        onRefresh: () => ref.refresh(ledgerProvider(key).future),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            asyncBody(
              ref.watch(ledgerProvider(key)),
              onRetry: () => ref.invalidate(ledgerProvider(key)),
              builder: (data) {
                final rows = (data['rows'] as List).cast<Json>();
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    HeroCard(
                      eyebrow: isBank
                          ? 'Current balance'
                          : 'Outstanding balance',
                      value: rupees(parseNum(data['balance'])),
                      caption:
                          '${rows.length} ${rows.length == 1 ? 'transaction' : 'transactions'}',
                      figures: const [],
                    ),
                    const SectionTitle('Transactions'),
                    if (rows.isEmpty)
                      const ReportCard(
                        child: Text(
                          'Nothing recorded yet.',
                          style: TextStyle(color: AppColors.textSecondary),
                        ),
                      )
                    else
                      ReportCard(
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        child: Column(
                          children: [
                            for (final r in rows)
                              _LedgerTile(row: r, isBank: isBank),
                          ],
                        ),
                      ),
                  ],
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _LedgerTile extends StatelessWidget {
  const _LedgerTile({required this.row, required this.isBank});

  final Json row;
  final bool isBank;

  @override
  Widget build(BuildContext context) {
    final debit = parseNum(row['debit']);
    final credit = parseNum(row['credit']);
    // Bank: debit is money in. Vendor: debit is a bill raised (money owed), credit a payment made.
    final increases = debit > 0;
    final money = increases ? debit : credit;
    final good = isBank ? increases : !increases;
    final color = good ? AppColors.sale : AppColors.expense;
    final date = DateTime.parse(row['date'] as String);

    return InkWell(
      onTap: () => context.push(dayLocation(row['date'] as String)),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Row(
          children: [
            IconBadge(
              icon: increases ? Icons.add_rounded : Icons.remove_rounded,
              color: color,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${row['particular']}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${displayDate(date)} · Balance ${rupees(parseNum(row['balance']))}',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              '${increases ? '+' : '−'}${rupees(money)}',
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w800,
                color: color,
              ),
            ),
            const TapChevron(),
          ],
        ),
      ),
    );
  }
}
