import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../app/router.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/utils/date_formatter.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../../shared/widgets/report_widgets.dart';
import '../../reports/data/reports_repository.dart' show Json;
import '../application/requests_providers.dart';

/// The Requests tab: what customers sent, for today by default, with a day filter, search and
/// a one-tap filter for what still needs a quote or a payment.
class RequestsScreen extends ConsumerStatefulWidget {
  const RequestsScreen({super.key});

  @override
  ConsumerState<RequestsScreen> createState() => _RequestsScreenState();
}

class _RequestsScreenState extends ConsumerState<RequestsScreen> {
  final _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _refresh() {
    ref.invalidate(waitingCountProvider);
    return ref.refresh(requestsListProvider.future);
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: DateTime(now.year - 2),
      lastDate: now,
    );
    if (picked == null) return;
    final iso = isoDate(picked);
    final yesterday = now.subtract(const Duration(days: 1));
    final day = iso == isoDate(now)
        ? 'today'
        : iso == isoDate(yesterday)
        ? 'yesterday'
        : iso;
    ref.read(requestsFilterProvider.notifier).setDay(day);
  }

  @override
  Widget build(BuildContext context) {
    final filter = ref.watch(requestsFilterProvider);
    final notifier = ref.read(requestsFilterProvider.notifier);
    final isToday = filter.day.isEmpty || filter.day == 'today';
    final isCustom =
        filter.day.isNotEmpty &&
        !const ['today', 'yesterday', 'all'].contains(filter.day);

    return ReportPage(
      title: 'Customer requests',
      onRefresh: _refresh,
      filters: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _DayChip(
              label: 'Today',
              selected: isToday,
              onTap: () => notifier.setDay('today'),
            ),
            _DayChip(
              label: 'Yesterday',
              selected: filter.day == 'yesterday',
              onTap: () => notifier.setDay('yesterday'),
            ),
            _DayChip(
              label: isCustom
                  ? displayDate(DateTime.parse(filter.day))
                  : 'Pick a date',
              icon: Icons.calendar_today_outlined,
              selected: isCustom,
              onTap: _pickDate,
            ),
            _DayChip(
              label: 'All days',
              selected: filter.day == 'all',
              onTap: () => notifier.setDay('all'),
            ),
          ],
        ),
        TextField(
          controller: _search,
          textInputAction: TextInputAction.search,
          onChanged: (_) => setState(() {}),
          onSubmitted: notifier.setQuery,
          decoration: InputDecoration(
            hintText: 'Search name, phone, order no., item or address',
            prefixIcon: const Icon(Icons.search),
            suffixIcon: _search.text.isEmpty && filter.query.isEmpty
                ? null
                : IconButton(
                    tooltip: 'Clear search',
                    icon: const Icon(Icons.close),
                    onPressed: () {
                      _search.clear();
                      notifier.setQuery('');
                      setState(() {});
                    },
                  ),
          ),
        ),
      ],
      children: [
        asyncBody(
          ref.watch(requestsListProvider),
          onRetry: () => ref.invalidate(requestsListProvider),
          builder: (data) => _Body(data: data, filter: filter),
        ),
      ],
    );
  }
}

class _DayChip extends StatelessWidget {
  const _DayChip({
    required this.label,
    required this.selected,
    required this.onTap,
    this.icon,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => ChoiceChip(
    avatar: icon == null
        ? null
        : Icon(
            icon,
            size: 16,
            color: selected ? AppColors.onBrand : AppColors.textSecondary,
          ),
    label: Text(label),
    selected: selected,
    showCheckmark: false,
    selectedColor: AppColors.brand,
    labelStyle: TextStyle(
      fontWeight: FontWeight.w700,
      color: selected ? AppColors.onBrand : AppColors.textPrimary,
    ),
    onSelected: (_) => onTap(),
  );
}

class _Body extends ConsumerWidget {
  const _Body({required this.data, required this.filter});

  final Json data;
  final RequestsFilter filter;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final counts = data['counts'] as Json;
    final amounts = data['amounts'] as Json;
    final rows = (data['requests'] as List).cast<Json>();
    final label = (data['day'] as Json)['label'] as String;
    final notifier = ref.read(requestsFilterProvider.notifier);

    String selected() {
      if (filter.payment == 'PAID') return 'paid';
      if (filter.payment == 'UNPAID') return 'unpaid';
      if (filter.status == 'PENDING') return 'waiting';
      if (filter.status == 'QUOTED') return 'quoted';
      return 'all';
    }

    Widget chip(String key, String text, int count, VoidCallback onTap) =>
        ChoiceChip(
          label: Text('$text $count'),
          selected: selected() == key,
          showCheckmark: false,
          selectedColor: AppColors.brandSoft,
          labelStyle: TextStyle(
            fontWeight: FontWeight.w700,
            color: selected() == key ? AppColors.brand : AppColors.textPrimary,
          ),
          onSelected: (_) => onTap(),
        );

    int n(String key) => (counts[key] as num?)?.toInt() ?? 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Showing: $label',
          style: const TextStyle(
            color: AppColors.textSecondary,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            chip('all', 'All', n('total'), () => notifier.setStatus()),
            chip(
              'waiting',
              'Waiting',
              n('pending'),
              () => notifier.setStatus(status: 'PENDING'),
            ),
            chip(
              'quoted',
              'Quoted',
              n('quoted'),
              () => notifier.setStatus(status: 'QUOTED'),
            ),
            chip(
              'unpaid',
              'Unpaid',
              n('unpaid'),
              () => notifier.setStatus(payment: 'UNPAID'),
            ),
            chip(
              'paid',
              'Paid',
              n('paid'),
              () => notifier.setStatus(payment: 'PAID'),
            ),
          ],
        ),
        const SizedBox(height: 14),
        StatGrid(
          tiles: [
            StatTile(
              label: 'Quoted amount',
              value: rupees(parseNum(amounts['total'])),
              color: AppColors.brand,
            ),
            StatTile(
              label: 'Paid',
              value: rupees(parseNum(amounts['paid'])),
              color: AppColors.sale,
              hint: 'Unpaid ${rupees(parseNum(amounts['unpaid']))}',
            ),
          ],
        ),
        const SectionTitle('Requests'),
        if (rows.isEmpty)
          ReportCard(
            child: Text(
              filter.query.isNotEmpty
                  ? 'No requests match "${filter.query}".'
                  : 'Nothing here for this day. Try another day, or choose All days.',
            ),
          ),
        for (final r in rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _RequestCard(request: r),
          ),
      ],
    );
  }
}

String _stamp(String iso) {
  final t = DateTime.parse(iso).toLocal();
  final h = t.hour.toString().padLeft(2, '0');
  final m = t.minute.toString().padLeft(2, '0');
  return '${displayDate(t).substring(0, displayDate(t).length - 5)}, $h:$m';
}

class _RequestCard extends StatelessWidget {
  const _RequestCard({required this.request});

  final Json request;

  @override
  Widget build(BuildContext context) {
    final number = request['number'] as int;
    final name = request['customer_name'] as String;
    final quoted = request['status'] == 'QUOTED';
    final paid = request['payment_status'] == 'PAID';
    final items = request['item_count'] as int;

    final (status, color) = paid
        ? ('Paid', AppColors.sale)
        : quoted
        ? ('Quoted · unpaid', AppColors.brand)
        : ('Waiting for quote', AppColors.purchase);

    return ReportCard(
      onTap: () => context.push('${AppRoutes.request}/$number'),
      child: Row(
        children: [
          CircleAvatar(
            backgroundColor: AppColors.brandSoft,
            foregroundColor: AppColors.brand,
            child: Text(
              name.isEmpty ? '?' : name.characters.first.toUpperCase(),
              style: const TextStyle(fontWeight: FontWeight.w800),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 15,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  '#$number · ${request['customer_phone']} · $items item${items == 1 ? '' : 's'}',
                  style: const TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 12,
                  ),
                ),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: color.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Text(
                        status,
                        style: TextStyle(
                          color: color,
                          fontSize: 11,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ),
                    if (request['edited'] == true)
                      const Text(
                        'edited',
                        style: TextStyle(
                          color: AppColors.textSecondary,
                          fontSize: 11,
                        ),
                      ),
                  ],
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                quoted ? rupees(parseNum(request['amount'])) : '-',
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 15,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                _stamp(request['created_at'] as String),
                style: const TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 11,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
