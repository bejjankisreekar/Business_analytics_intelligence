import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../../app/theme/app_colors.dart';

/// What the bill did with a scan: a message to show on the scanner, and how many items are on the bill.
typedef ScanOutcome = ({String message, bool error, int count});

/// A full-screen camera, like a payment-app scanner: a frame to aim at the barcode, a torch,
/// and the result of each scan shown at the bottom. It stays open so several items can be
/// scanned one after another; "Done" returns to the bill, which has already been filled in.
class FullScreenScanner extends StatefulWidget {
  const FullScreenScanner({
    super.key,
    required this.title,
    required this.onScan,
  });

  final String title;

  /// Handles one scanned code (looks it up and fills the bill) and says what happened.
  final Future<ScanOutcome> Function(String code) onScan;

  @override
  State<FullScreenScanner> createState() => _FullScreenScannerState();
}

class _FullScreenScannerState extends State<FullScreenScanner> {
  final _controller = MobileScannerController();

  bool _busy = false;
  String _lastCode = '';
  DateTime _lastAt = DateTime.fromMillisecondsSinceEpoch(0);
  ScanOutcome? _outcome;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_busy) return;
    final code = capture.barcodes.firstOrNull?.rawValue;
    if (code == null || code.isEmpty) return;
    // Holding the camera on one label reports it many times a second; take it once,
    // and take it again only after a short pause so the same item can be scanned twice.
    final now = DateTime.now();
    if (code == _lastCode && now.difference(_lastAt) < const Duration(seconds: 2)) {
      return;
    }
    _lastCode = code;
    _lastAt = now;
    setState(() => _busy = true);
    HapticFeedback.mediumImpact();
    final outcome = await widget.onScan(code);
    if (!mounted) return;
    setState(() {
      _outcome = outcome;
      _busy = false;
    });
    _lastAt = DateTime.now();
  }

  @override
  Widget build(BuildContext context) {
    final outcome = _outcome;
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          MobileScanner(
            controller: _controller,
            onDetect: _onDetect,
            placeholderBuilder: (context) => const _ScannerMessage(
              message: 'Starting camera…',
              hint: 'If the camera does not appear, allow camera access for Finday '
                  'in your phone settings (Apps > Finday > Permissions).',
            ),
            errorBuilder: (context, error) {
              final denied =
                  error.errorCode == MobileScannerErrorCode.permissionDenied;
              return _ScannerMessage(
                message: denied
                    ? 'Camera permission is off. Allow the camera for Finday in your '
                          'phone settings (Apps > Finday > Permissions), then try again.'
                    : 'The camera could not start (${error.errorCode.message}).',
                onRetry: () => _controller.start(),
              );
            },
          ),
          const IgnorePointer(child: _ScanFrame()),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              child: Row(
                children: [
                  IconButton(
                    tooltip: 'Close',
                    color: Colors.white,
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                  Expanded(
                    child: Text(
                      widget.title,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  ValueListenableBuilder<MobileScannerState>(
                    valueListenable: _controller,
                    builder: (context, state, _) {
                      final on = state.torchState == TorchState.on;
                      return IconButton(
                        tooltip: on ? 'Turn torch off' : 'Turn torch on',
                        color: Colors.white,
                        icon: Icon(on ? Icons.flash_on : Icons.flash_off),
                        onPressed: state.torchState == TorchState.unavailable
                            ? null
                            : () => _controller.toggleTorch(),
                      );
                    },
                  ),
                ],
              ),
            ),
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: SafeArea(
              child: Container(
                margin: const EdgeInsets.all(16),
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      _busy
                          ? 'Looking up…'
                          : (outcome?.message.isNotEmpty ?? false)
                          ? outcome!.message
                          : 'Point the camera at a barcode. It is added to the bill straight away.',
                      style: TextStyle(
                        fontSize: 14,
                        color: outcome?.error ?? false
                            ? AppColors.danger
                            : AppColors.textPrimary,
                      ),
                    ),
                    const SizedBox(height: 12),
                    FilledButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: Text(
                        (outcome?.count ?? 0) > 0
                            ? 'Done · ${outcome!.count} on the bill'
                            : 'Done',
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A dimmed screen with a clear rounded window and corner marks, to aim the barcode into.
class _ScanFrame extends StatelessWidget {
  const _ScanFrame();

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth * 0.8;
        final window = Rect.fromCenter(
          center: Offset(constraints.maxWidth / 2, constraints.maxHeight * 0.4),
          width: width,
          height: width * 0.62,
        );
        return CustomPaint(
          size: Size(constraints.maxWidth, constraints.maxHeight),
          painter: _FramePainter(window),
        );
      },
    );
  }
}

class _FramePainter extends CustomPainter {
  _FramePainter(this.window);

  final Rect window;

  @override
  void paint(Canvas canvas, Size size) {
    final hole = RRect.fromRectAndRadius(window, const Radius.circular(18));
    final dim = Path()
      ..addRect(Offset.zero & size)
      ..addRRect(hole)
      ..fillType = PathFillType.evenOdd;
    canvas.drawPath(dim, Paint()..color = Colors.black54);

    final mark = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round;
    const len = 26.0;
    final l = window.left, t = window.top, r = window.right, b = window.bottom;
    for (final corner in [
      [Offset(l, t + len), Offset(l, t), Offset(l + len, t)],
      [Offset(r - len, t), Offset(r, t), Offset(r, t + len)],
      [Offset(l, b - len), Offset(l, b), Offset(l + len, b)],
      [Offset(r - len, b), Offset(r, b), Offset(r, b - len)],
    ]) {
      canvas.drawPath(
        Path()
          ..moveTo(corner[0].dx, corner[0].dy)
          ..lineTo(corner[1].dx, corner[1].dy)
          ..lineTo(corner[2].dx, corner[2].dy),
        mark,
      );
    }
  }

  @override
  bool shouldRepaint(_FramePainter old) => old.window != window;
}

/// Shown instead of the camera picture while it starts or when it can't start.
class _ScannerMessage extends StatelessWidget {
  const _ScannerMessage({required this.message, this.hint, this.onRetry});

  final String message;
  final String? hint;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: AppColors.ink,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                message,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white, fontSize: 15),
              ),
              if (hint != null) ...[
                const SizedBox(height: 8),
                Text(
                  hint!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white70, fontSize: 12),
                ),
              ],
              if (onRetry != null) ...[
                const SizedBox(height: 14),
                OutlinedButton(
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.white),
                  onPressed: onRetry,
                  child: const Text('Try again'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
