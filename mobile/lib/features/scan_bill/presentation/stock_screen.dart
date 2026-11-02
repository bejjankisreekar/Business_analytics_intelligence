import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../app/theme/app_colors.dart';
import '../../../core/network/api_exception.dart';
import '../../../shared/widgets/error_view.dart';
import '../../../shared/widgets/report_blueprint.dart';
import '../../entries/application/entries_providers.dart';
import '../models/inventory_item.dart';
import '../models/stock_unit.dart';

/// Filter for the phone list: status ('' for all, 'IN_STOCK' or 'SOLD') and the search text.
typedef StockFilter = ({String status, String query});

final stockProvider = FutureProvider.autoDispose.family<StockPage, StockFilter>((ref, filter) {
  return ref.watch(stockRepositoryProvider).units(
    status: filter.status,
    query: filter.query,
  );
});

final inventoryProvider = FutureProvider.autoDispose<InventoryPage>((ref) {
  return ref.watch(stockRepositoryProvider).inventory();
});

/// Inventory and stock. "Items" shows what was bought, sold and is left for every item;
/// "Phones" lists each phone by IMEI, in stock or sold, with the bill it went on.
class StockScreen extends ConsumerStatefulWidget {
  const StockScreen({super.key});

  @override
  ConsumerState<StockScreen> createState() => _StockScreenState();
}

class _StockScreenState extends ConsumerState<StockScreen> {
  String _tab = 'items';
  String _status = '';
  final _search = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final filter = (status: _status, query: _query);
    // Phones are only tracked by IMEI for a mobiles organization.
    final phonesAvailable =
        ref.watch(entryOptionsProvider).valueOrNull?.tracksPhones ?? false;
    final phones = _tab == 'phones' && phonesAvailable;

    return ReportPage(
      title: 'Inventory & stock',
      onRefresh: () async {
        if (phones) {
          ref.invalidate(stockProvider(filter));
          await ref.read(stockProvider(filter).future);
        } else {
          ref.invalidate(inventoryProvider);
          await ref.read(inventoryProvider.future);
        }
      },
      tabs: phonesAvailable
          ? TabPills<String>(
              selected: _tab,
              onChanged: (value) => setState(() => _tab = value),
              items: const [
                TabPillItem(
                  value: 'items',
                  label: 'Items',
                  icon: Icons.inventory_2_outlined,
                ),
                TabPillItem(
                  value: 'phones',
                  label: 'Phones (IMEI)',
                  icon: Icons.smartphone,
                ),
              ],
            )
          : null,
      filters: phones
          ? [
              TabPills<String>(
                selected: _status,
                onChanged: (value) => setState(() => _status = value),
                items: const [
                  TabPillItem(
                    value: '',
                    label: 'All',
                    icon: Icons.list_alt,
                  ),
                  TabPillItem(
                    value: 'IN_STOCK',
                    label: 'In stock',
                    icon: Icons.check_circle_outline,
                    color: AppColors.sale,
                  ),
                  TabPillItem(
                    value: 'SOLD',
                    label: 'Sold',
                    icon: Icons.sell_outlined,
                    color: AppColors.purchase,
                  ),
                ],
              ),
              TextField(
                controller: _search,
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(
                  hintText: 'Search by IMEI or model',
                  prefixIcon: Icon(Icons.search),
                ),
                onSubmitted: (value) => setState(() => _query = value.trim()),
              ),
            ]
          : null,
      children: [
        if (phones)
          ref.watch(stockProvider(filter)).when(
            loading: _loading,
            error: (error, _) => ErrorView(
              message: userMessage(error),
              onRetry: () => ref.invalidate(stockProvider(filter)),
            ),
            data: (page) => _PhoneList(page: page),
          )
        else
          ref.watch(inventoryProvider).when(
            loading: _loading,
            error: (error, _) => ErrorView(
              message: userMessage(error),
              onRetry: () => ref.invalidate(inventoryProvider),
            ),
            data: (page) => _ItemList(page: page),
          ),
      ],
    );
  }

  Widget _loading() => const Padding(
    padding: EdgeInsets.all(32),
    child: Center(child: CircularProgressIndicator()),
  );
}

class _ItemList extends StatelessWidget {
  const _ItemList({required this.page});

  final InventoryPage page;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(16),
          margin: const EdgeInsets.only(bottom: 12),
          decoration: BoxDecoration(
            color: AppColors.card,
            borderRadius: BorderRadius.circular(20),
            boxShadow: AppColors.cardShadow,
          ),
          child: Row(
            children: [
              _Stat(label: 'Bought', value: '${page.purchased}'),
              _Stat(label: 'Sold', value: '${page.sold}'),
              _Stat(
                label: 'In stock',
                value: '${page.inStock}',
                color: AppColors.sale,
              ),
              _Stat(
                label: 'Stock value',
                value: '₹ ${page.stockValue.toStringAsFixed(0)}',
              ),
            ],
          ),
        ),
        if (page.items.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'No items yet. Items appear here once they are added on Scan & bill.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary),
            ),
          ),
        for (final item in page.items) _ItemTile(item: item),
      ],
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.color});

  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(
            value,
            style: TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w800,
              color: color ?? AppColors.textPrimary,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
          ),
        ],
      ),
    );
  }
}

class _ItemTile extends StatelessWidget {
  const _ItemTile({required this.item});

  final InventoryItem item;

  @override
  Widget build(BuildContext context) {
    final low = item.inStock <= 0;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(16),
        boxShadow: AppColors.cardShadow,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.name,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                if (item.attributes.isNotEmpty || item.path.isNotEmpty || item.tracked)
                  Text(
                    [
                      if (item.attributes.isNotEmpty) item.attributes,
                      if (item.path.isNotEmpty) item.path,
                      if (item.tracked) 'by IMEI',
                    ].join(' · '),
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                const SizedBox(height: 4),
                Text(
                  'Bought ${item.purchased} · Sold ${item.sold}',
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '${item.inStock}',
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                  color: low ? AppColors.danger : AppColors.sale,
                ),
              ),
              const Text(
                'in stock',
                style: TextStyle(fontSize: 11, color: AppColors.textSecondary),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PhoneList extends StatelessWidget {
  const _PhoneList({required this.page});

  final StockPage page;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          '${page.inStock} in stock · ${page.sold} sold',
          style: const TextStyle(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 12),
        if (page.units.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'No phones here yet. Record them on a purchase by scanning each IMEI.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary),
            ),
          ),
        for (final unit in page.units) _UnitTile(unit: unit),
      ],
    );
  }
}

class _UnitTile extends StatelessWidget {
  const _UnitTile({required this.unit});

  final StockUnit unit;

  @override
  Widget build(BuildContext context) {
    final color = unit.sold ? AppColors.purchase : AppColors.sale;
    final bill = unit.sold ? unit.saleBillNo : unit.purchaseBillNo;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(16),
        boxShadow: AppColors.cardShadow,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  unit.description.isNotEmpty ? unit.description : unit.product,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 2),
                Text(
                  'IMEI ${unit.serial}${unit.imei2.isEmpty ? '' : '  ·  IMEI 2 ${unit.imei2}'}',
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
                if (unit.other.isNotEmpty)
                  Text(
                    unit.other,
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                if (bill.isNotEmpty)
                  Text(
                    '${unit.sold ? 'Sold' : 'Bought'} on bill $bill',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(999),
            ),
            child: Text(
              unit.sold ? 'Sold' : 'In stock',
              style: TextStyle(
                color: color,
                fontWeight: FontWeight.w700,
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
