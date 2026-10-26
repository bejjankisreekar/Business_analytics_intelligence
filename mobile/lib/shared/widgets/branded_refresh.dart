import 'package:flutter/material.dart';

import '../../app/theme/app_colors.dart';
import 'finday_logo.dart';

/// Drop-in replacement for [RefreshIndicator] around a scrollable.
///
/// Pulling down bounces the page and reveals the Finday Intelligence mark behind it; let go past
/// the threshold and the page refreshes, with a slim branded bar shown while it loads.
class BrandedRefresh extends StatefulWidget {
  const BrandedRefresh({
    super.key,
    required this.onRefresh,
    required this.child,
  });

  final Future<void> Function() onRefresh;

  /// A vertical scrollable (usually a `ListView`).
  final Widget child;

  @override
  State<BrandedRefresh> createState() => _BrandedRefreshState();
}

class _BrandedRefreshState extends State<BrandedRefresh> {
  static const _trigger = 80.0;

  final _pull = ValueNotifier<double>(0);
  bool _refreshing = false;

  @override
  void dispose() {
    _pull.dispose();
    super.dispose();
  }

  /// Furthest the page was pulled during the current drag.
  double _peak = 0;

  bool _onScroll(ScrollNotification n) {
    if (n.depth != 0 || n.metrics.axis != Axis.vertical) return false;
    final pulled = n.metrics.pixels < 0 ? -n.metrics.pixels : 0.0;
    if (n is ScrollUpdateNotification || n is OverscrollNotification) {
      final dragging = n is ScrollUpdateNotification
          ? n.dragDetails != null
          : (n as OverscrollNotification).dragDetails != null;
      _pull.value = pulled;
      if (dragging) {
        if (pulled > _peak) _peak = pulled;
      } else if (_peak >= _trigger && !_refreshing) {
        // Let go past the threshold: refresh once.
        _peak = 0;
        _run();
      }
    } else if (n is ScrollEndNotification) {
      _peak = 0;
      _pull.value = 0;
    }
    return false;
  }

  Future<void> _run() async {
    setState(() => _refreshing = true);
    try {
      await widget.onRefresh();
    } catch (_) {
      // The screen shows its own error state.
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        AnimatedSize(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
          child: _refreshing
              ? const _Brand(progress: 1, spinning: true)
              : const SizedBox(width: double.infinity),
        ),
        Expanded(
          child: Stack(
            children: [
              Positioned(
                top: 0,
                left: 0,
                right: 0,
                child: ValueListenableBuilder<double>(
                  valueListenable: _pull,
                  builder: (_, pulled, _) => pulled <= 0 || _refreshing
                      ? const SizedBox.shrink()
                      : _Brand(
                          progress: (pulled / _trigger).clamp(0, 1).toDouble(),
                          ready: pulled >= _trigger,
                        ),
                ),
              ),
              NotificationListener<ScrollNotification>(
                onNotification: _onScroll,
                child: ScrollConfiguration(
                  behavior: const _BounceBehavior(),
                  child: widget.child,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Bouncing overscroll on every platform, so the page can be pulled down on Android too.
class _BounceBehavior extends MaterialScrollBehavior {
  const _BounceBehavior();

  @override
  ScrollPhysics getScrollPhysics(BuildContext context) =>
      const BouncingScrollPhysics(parent: AlwaysScrollableScrollPhysics());

  @override
  Widget buildOverscrollIndicator(
    BuildContext context,
    Widget child,
    ScrollableDetails details,
  ) => child;
}

class _Brand extends StatelessWidget {
  const _Brand({
    required this.progress,
    this.ready = false,
    this.spinning = false,
  });

  /// 0..1 while pulling.
  final double progress;
  final bool ready;
  final bool spinning;

  @override
  Widget build(BuildContext context) {
    const label = 'Refreshing…';
    return Opacity(
      opacity: progress.clamp(0.15, 1),
      child: Container(
        width: double.infinity,
        height: spinning ? 56 : 72,
        alignment: Alignment.center,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Transform.scale(
              scale: 0.7 + 0.3 * progress,
              child: const FindayLogo(size: 30),
            ),
            const SizedBox(width: 10),
            Text(
              label,
              style: const TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w600,
                color: AppColors.brandDark,
              ),
            ),
            if (spinning) ...[
              const SizedBox(width: 12),
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.brand,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
