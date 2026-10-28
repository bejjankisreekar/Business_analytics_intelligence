import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/router.dart';
import '../../app/theme/app_colors.dart';
import '../../features/auth/application/session_controller.dart';
import '../../features/reports/application/reports_providers.dart';
import '../../features/requests/application/requests_providers.dart';

/// Hosts the four main tabs with a bottom bar and a raised "+" button in the middle for new entries.
class MainShell extends ConsumerWidget {
  const MainShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  // Each tab opens one router branch (see router.dart). The Requests tab only shows for accounts
  // that have customer requests, so branch 3 is simply left out of the bar for everyone else.
  static const _dashboard = _TabItem(
    0,
    Icons.space_dashboard_outlined,
    Icons.space_dashboard_rounded,
    'Dashboard',
  );
  static const _daily = _TabItem(
    1,
    Icons.bar_chart_outlined,
    Icons.bar_chart_rounded,
    'Daily',
  );
  static const _intelligence = _TabItem(
    2,
    Icons.auto_graph_outlined,
    Icons.auto_graph_rounded,
    'Intelligence',
  );
  static const _requests = _TabItem(
    3,
    Icons.receipt_long_outlined,
    Icons.receipt_long_rounded,
    'Requests',
  );
  static const _more = _TabItem(
    4,
    Icons.grid_view_outlined,
    Icons.grid_view_rounded,
    'More',
  );

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final hasRequests = ref.watch(
      sessionControllerProvider.select((s) => s.user?.customerRequests ?? false),
    );
    final waiting = hasRequests
        ? (ref.watch(waitingCountProvider).valueOrNull ?? 0)
        : 0;

    Widget tab(_TabItem item) => Expanded(
      child: _NavButton(
        item: item,
        selected: shell.currentIndex == item.branch,
        badge: item == _requests ? waiting : 0,
        onTap: () => shell.goBranch(
          item.branch,
          initialLocation: item.branch == shell.currentIndex,
        ),
      ),
    );

    return Scaffold(
      body: shell,
      bottomNavigationBar: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: Container(
            height: 68,
            decoration: BoxDecoration(
              color: AppColors.card,
              borderRadius: BorderRadius.circular(30),
              border: Border.all(color: AppColors.divider),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x261E1B4B),
                  blurRadius: 28,
                  offset: Offset(0, 10),
                ),
              ],
            ),
            child: Stack(
              clipBehavior: Clip.none,
              alignment: Alignment.center,
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  child: Row(
                    children: [
                      tab(_dashboard),
                      tab(_daily),
                      const Expanded(child: SizedBox()),
                      tab(_intelligence),
                      if (hasRequests) tab(_requests),
                      tab(_more),
                    ],
                  ),
                ),
                Positioned(
                  top: -20,
                  child: Semantics(
                    button: true,
                    label: 'Add entry',
                    child: GestureDetector(
                      onTap: () => context
                          .push(AppRoutes.entries)
                          .then((_) => ref.invalidate(dashboardProvider)),
                      child: Container(
                        width: 58,
                        height: 58,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: AppColors.heroGradient,
                          border: Border.all(
                            color: AppColors.surface,
                            width: 5,
                          ),
                          boxShadow: const [
                            BoxShadow(
                              color: Color(0x664338CA),
                              blurRadius: 18,
                              offset: Offset(0, 8),
                            ),
                          ],
                        ),
                        child: const Icon(
                          Icons.add_rounded,
                          color: AppColors.onBrand,
                          size: 30,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TabItem {
  const _TabItem(this.branch, this.icon, this.activeIcon, this.label);

  /// Which router branch this tab opens.
  final int branch;
  final IconData icon;
  final IconData activeIcon;
  final String label;
}

class _NavButton extends StatelessWidget {
  const _NavButton({
    required this.item,
    required this.selected,
    required this.onTap,
    this.badge = 0,
  });

  final _TabItem item;
  final bool selected;
  final VoidCallback onTap;

  /// A count shown on the icon (0 shows nothing).
  final int badge;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Center(
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOutCubic,
          width: double.infinity,
          margin: const EdgeInsets.symmetric(horizontal: 3, vertical: 8),
          padding: const EdgeInsets.symmetric(vertical: 6),
          decoration: BoxDecoration(
            gradient: selected ? AppColors.heroGradient : null,
            borderRadius: BorderRadius.circular(22),
            boxShadow: selected
                ? const [
                    BoxShadow(
                      color: Color(0x404338CA),
                      blurRadius: 10,
                      offset: Offset(0, 4),
                    ),
                  ]
                : null,
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Badge(
                isLabelVisible: badge > 0,
                label: Text(badge > 99 ? '99+' : '$badge'),
                backgroundColor: AppColors.purchase,
                textColor: Colors.white,
                child: Icon(
                  selected ? item.activeIcon : item.icon,
                  color: selected ? Colors.white : AppColors.textSecondary,
                  size: 22,
                ),
              ),
              const SizedBox(height: 2),
              FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  item.label,
                  maxLines: 1,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: selected ? FontWeight.w800 : FontWeight.w600,
                    color: selected ? Colors.white : AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
