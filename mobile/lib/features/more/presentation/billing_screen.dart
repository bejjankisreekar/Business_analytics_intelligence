import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/application/reports_providers.dart';
import '../../reports/data/reports_repository.dart';

/// Plan, subscription status, invoices and payments. Read-only; paying is done on the web app.
class BillingScreen extends ConsumerWidget {
  const BillingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ReportPage(
      title: 'Billing',
      onRefresh: () => ref.refresh(billingProvider.future),
      children: [
        asyncBody(
          ref.watch(billingProvider),
          onRetry: () => ref.invalidate(billingProvider),
          builder: (data) => _Content(data: data),
        ),
      ],
    );
  }
}

String _date(Object? iso) {
  final d = DateTime.tryParse('$iso');
  return d == null ? '—' : displayDate(d);
}

Color _statusColor(String status) => switch (status) {
  'ACTIVE' || 'PAID' || 'SUCCESS' => AppColors.sale,
  'TRIAL' || 'ISSUED' || 'PENDING' || 'PARTIALLY_PAID' => AppColors.brand,
  'PAYMENT_DUE' || 'OVERDUE' || 'PAST_DUE' => AppColors.purchase,
  _ => AppColors.expense,
};

class _Content extends StatelessWidget {
  const _Content({required this.data});

  final Json data;

  @override
  Widget build(BuildContext context) {
    final sub = data['subscription'] as Json?;
    final invoices = (data['invoices'] as List).cast<Json>();
    final payments = (data['payments'] as List).cast<Json>();
    final outstanding = parseNum(data['outstanding']);
    final locked = data['locked'] == true;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (locked)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 14),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppColors.expense.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: AppColors.expense.withValues(alpha: 0.4),
              ),
            ),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.lock_outline, color: AppColors.expense, size: 20),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Your plan needs attention. Pay the outstanding invoice on the web app to unlock your account.',
                    style: TextStyle(fontWeight: FontWeight.w600, height: 1.35),
                  ),
                ),
              ],
            ),
          ),
        HeroCard(
          eyebrow: sub == null
              ? 'Billing'
              : '${sub['plan']} · ${sub['status_label']}',
          value: outstanding > 0 ? rupees(outstanding) : 'All paid',
          caption: outstanding > 0
              ? 'Outstanding'
              : sub == null
              ? 'No subscription yet'
              : _renewalLine(sub),
          figures: [
            (
              label: 'Invoices',
              value: '${invoices.length}',
              icon: Icons.receipt_long_outlined,
            ),
            (
              label: 'Payments',
              value: '${payments.length}',
              icon: Icons.payments_outlined,
            ),
          ],
        ),
        if (sub != null) ...[
          const SectionTitle('Your plan'),
          _PlanCard(sub: sub),
        ],
        SectionTitle(
          'Invoices',
          trailing: Text(
            '${invoices.length}',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
        ),
        if (invoices.isEmpty)
          const ReportCard(
            child: Text(
              'No invoices yet.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          )
        else
          for (final i in invoices)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _InvoiceCard(invoice: i),
            ),
        if (outstanding > 0) _PayOnWeb(url: '${data['pay_url']}'),
        const SectionTitle('Payments'),
        if (payments.isEmpty)
          const ReportCard(
            child: Text(
              'No payments recorded.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
          )
        else
          ReportCard(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              children: [for (final p in payments) _PaymentRow(payment: p)],
            ),
          ),
      ],
    );
  }
}

String _renewalLine(Json sub) {
  if (sub['status'] == 'TRIAL' && sub['trial_end_date'] != null) {
    return 'Trial ends ${_date(sub['trial_end_date'])}';
  }
  if (sub['end_date'] != null) {
    return '${sub['auto_renewal'] == true ? 'Renews' : 'Ends'} ${_date(sub['end_date'])}';
  }
  return '${sub['billing_cycle_label']} plan';
}

class _Chip extends StatelessWidget {
  const _Chip(this.label, this.status);

  final String label;
  final String status;

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w800,
          color: color,
        ),
      ),
    );
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.sub});

  final Json sub;

  @override
  Widget build(BuildContext context) {
    final features = (sub['features'] as List).cast<String>();
    final discount = parseNum(sub['discount']);
    final tax = parseNum(sub['tax']);
    return ReportCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '${sub['plan']}',
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              _Chip('${sub['status_label']}', '${sub['status']}'),
            ],
          ),
          const SizedBox(height: 6),
          LineRow('Billing', '${sub['billing_cycle_label']}'),
          LineRow('Started', _date(sub['start_date'])),
          if (sub['trial_end_date'] != null)
            LineRow('Trial ends', _date(sub['trial_end_date'])),
          LineRow(
            sub['auto_renewal'] == true ? 'Renews on' : 'Ends on',
            _date(sub['end_date']),
          ),
          LineRow('Payment', '${sub['payment_status_label']}'),
          LineRow('Auto-renewal', sub['auto_renewal'] == true ? 'On' : 'Off'),
          LineRow('Autopay', sub['autopay_enabled'] == true ? 'On' : 'Off'),
          if (sub['is_complimentary'] == true)
            const LineRow('Plan type', 'Complimentary'),
          const Divider(),
          LineRow('Price', rupees(parseNum(sub['price']))),
          if (discount > 0)
            LineRow('Discount', '− ${rupees(discount)}', color: AppColors.sale),
          if (tax > 0) LineRow('Tax', rupees(tax)),
          LineRow(
            'Total per cycle',
            rupees(parseNum(sub['final_amount'])),
            bold: true,
          ),
          if (sub['user_limit'] != null || sub['business_limit'] != null) ...[
            const Divider(),
            LineRow(
              'Users',
              sub['user_limit'] == null ? 'Unlimited' : '${sub['user_limit']}',
            ),
            LineRow(
              'Businesses',
              sub['business_limit'] == null
                  ? 'Unlimited'
                  : '${sub['business_limit']}',
            ),
          ],
          if (features.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final f in features)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: AppColors.brandSoft,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      f,
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: AppColors.brand,
                      ),
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _InvoiceCard extends StatelessWidget {
  const _InvoiceCard({required this.invoice});

  final Json invoice;

  @override
  Widget build(BuildContext context) {
    final due = parseNum(invoice['amount_due']);
    final unpaid = due > 0 && invoice['status'] != 'CANCELLED';
    return ReportCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '${invoice['number']}',
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              _Chip('${invoice['status_label']}', '${invoice['status']}'),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            invoice['period_start'] == null
                ? 'Issued ${_date(invoice['invoice_date'])}'
                : 'Service ${_date(invoice['period_start'])} – ${_date(invoice['period_end'])}',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: 8),
          LineRow('Total', rupees(parseNum(invoice['total'])), bold: true),
          if (parseNum(invoice['discount']) > 0)
            LineRow(
              'Discount',
              '− ${rupees(parseNum(invoice['discount']))}',
              color: AppColors.sale,
            ),
          LineRow('Paid', rupees(parseNum(invoice['amount_paid']))),
          if (unpaid)
            LineRow(
              'Due by ${_date(invoice['due_date'])}',
              rupees(due),
              bold: true,
              color: AppColors.expense,
            ),
        ],
      ),
    );
  }
}

class _PayOnWeb extends StatelessWidget {
  const _PayOnWeb({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) => ReportCard(
    child: Row(
      children: [
        const Icon(Icons.open_in_browser, color: AppColors.brand),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Pay on the web app',
                style: TextStyle(fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 2),
              Text(
                url,
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
        TextButton(
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: url));
            if (context.mounted) {
              ScaffoldMessenger.of(context)
                  .showSnackBar(const SnackBar(content: Text('Link copied')));
            }
          },
          child: const Text('Copy link'),
        ),
      ],
    ),
  );
}

class _PaymentRow extends StatelessWidget {
  const _PaymentRow({required this.payment});

  final Json payment;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
    child: Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                rupees(parseNum(payment['amount'])),
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 2),
              Text(
                [
                  _date(payment['date']),
                  '${payment['method']}',
                  if ('${payment['invoice']}'.isNotEmpty)
                    '${payment['invoice']}',
                ].join(' · '),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
        _Chip('${payment['status_label']}', '${payment['status']}'),
      ],
    ),
  );
}
