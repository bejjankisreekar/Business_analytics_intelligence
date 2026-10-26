import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/router.dart';
import '../../app/theme/app_colors.dart';
import '../../features/reports/application/reports_providers.dart';

/// Hosts the four main tabs with a bottom bar and a raised "+" button in the middle for new entries.
class MainShell extends ConsumerWidget {
  const MainShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  static const _tabs = [
    _TabItem(
      Icons.space_dashboard_outlined,
      Icons.space_dashboard_rounded,
      'Dashboard',
    ),
    _TabItem(Icons.bar_chart_outlined, Icons.bar_chart_rounded, 'Daily'),
    _TabItem(
      Icons.auto_graph_outlined,
      Icons.auto_graph_rounded,
      'Intelligence',
    ),
    _TabItem(Icons.grid_view_outlined, Icons.grid_view_rounded, 'More'),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    Widget tab(int index) => Expanded(
      child: _NavButton(
        item: _tabs[index],
        selected: shell.currentIndex == index,
        onTap: () =>
            shell.goBranch(index, initialLocation: index == shell.currentIndex),
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
                      tab(0),
                      tab(1),
                      const Expanded(child: SizedBox()),
                      tab(2),
                      tab(3),
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
  const _TabItem(this.icon, this.activeIcon, this.label);

  final IconData icon;
  final IconData activeIcon;
  final String label;
}

class _NavButton extends StatelessWidget {
  const _NavButton({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final _TabItem item;
  final bool selected;
  final VoidCallback onTap;

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
              Icon(
                selected ? item.activeIcon : item.icon,
                color: selected ? Colors.white : AppColors.textSecondary,
                size: 22,
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
