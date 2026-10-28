import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/push/push_service.dart';
import '../features/auth/application/session_controller.dart';
import 'config/env.dart';
import 'messenger.dart';
import 'router.dart';
import 'theme/app_theme.dart';

class App extends ConsumerWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Push notifications start once someone who may use customer requests is signed in.
    ref.listen<SessionState>(sessionControllerProvider, (previous, next) {
      if (next.status == SessionStatus.signedIn && (next.user?.customerRequests ?? false)) {
        ref.read(pushServiceProvider).start();
      }
    });
    return MaterialApp.router(
      title: Env.appName,
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: rootMessengerKey,
      theme: AppTheme.light(),
      routerConfig: ref.watch(routerProvider),
    );
  }
}
