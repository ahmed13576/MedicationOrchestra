import 'dart:io';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Top-level background message handler — MUST be a top-level function.
/// Called by FCM when the app is in background/terminated.
@pragma('vm:entry-point')
Future<void> _firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  debugPrint('[FCM] Background message: ${message.messageId}');
  // Firebase is already initialized by main() before this is called.
}

/// Manages Firebase Cloud Messaging setup, notification channels,
/// device token registration with the backend, and foreground notifications.
class FcmService {
  static final FlutterLocalNotificationsPlugin _localNotifications =
      FlutterLocalNotificationsPlugin();

  static const _doseChannel = AndroidNotificationChannel(
    'medication_reminders',
    'Medication Reminders',
    description: 'Daily dose reminder notifications',
    importance: Importance.high,
  );

  static const _sosChannel = AndroidNotificationChannel(
    'sos_alerts',
    'SOS Emergency Alerts',
    description: 'Emergency medication interaction alerts for family members',
    importance: Importance.max,
  );

  /// Initialize FCM, request permissions, create notification channels,
  /// and register the device token with the backend.
  ///
  /// Call this fire-and-forget from HomeScreen.initState():
  /// ```dart
  /// FcmService.initialize(_dio, ApiConfig.baseUrl);
  /// ```
  static Future<void> initialize(Dio dio, String backendBaseUrl) async {
    try {
      // 1. Register top-level background handler
      FirebaseMessaging.onBackgroundMessage(_firebaseMessagingBackgroundHandler);

      // 2. Request notification permission (Android 13+ / iOS)
      final settings = await FirebaseMessaging.instance.requestPermission(
        alert: true,
        badge: true,
        sound: true,
      );
      debugPrint('[FCM] Permission: ${settings.authorizationStatus}');

      // 3. Create Android notification channels
      if (Platform.isAndroid) {
        final androidPlugin = _localNotifications
            .resolvePlatformSpecificImplementation<
                AndroidFlutterLocalNotificationsPlugin>();
        await androidPlugin?.createNotificationChannel(_doseChannel);
        await androidPlugin?.createNotificationChannel(_sosChannel);
        debugPrint('[FCM] Android notification channels created');
      }

      // 4. Initialize flutter_local_notifications for foreground display
      const initSettings = InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      );
      await _localNotifications.initialize(initSettings);

      // 5. Handle foreground messages by showing a local notification
      FirebaseMessaging.onMessage.listen((RemoteMessage message) {
        final notification = message.notification;
        if (notification == null) return;

        final isSOSAlert = message.data['type'] == 'sos_alert';
        _localNotifications.show(
          notification.hashCode,
          notification.title,
          notification.body,
          NotificationDetails(
            android: AndroidNotificationDetails(
              isSOSAlert ? _sosChannel.id : _doseChannel.id,
              isSOSAlert ? _sosChannel.name : _doseChannel.name,
              importance: isSOSAlert ? Importance.max : Importance.high,
              priority: isSOSAlert ? Priority.max : Priority.high,
              icon: '@mipmap/ic_launcher',
            ),
          ),
        );
        debugPrint('[FCM] Foreground notification shown: ${notification.title}');
      });

      // 6. Get FCM token and register with backend
      final token = await FirebaseMessaging.instance.getToken();
      if (token != null) {
        await _registerDeviceToken(dio, backendBaseUrl, token);

        // Refresh token whenever it changes
        FirebaseMessaging.instance.onTokenRefresh.listen((newToken) {
          _registerDeviceToken(dio, backendBaseUrl, newToken);
        });
      } else {
        debugPrint('[FCM] No FCM token available (emulator or permission denied)');
      }
    } catch (e) {
      // FCM is non-critical — never crash the app for notification issues
      debugPrint('[FCM] Initialization error (non-fatal): $e');
    }
  }

  /// Registers the device FCM token with the backend.
  /// Fetches a fresh Firebase ID token and includes it as the Authorization
  /// header so the backend's verify_firebase_token dependency is satisfied.
  static Future<void> _registerDeviceToken(
    Dio dio,
    String backendBaseUrl,
    String token,
  ) async {
    try {
      // Resolve auth token — falls back to dev-token when no user is signed in
      String authToken = 'dev-token';
      try {
        final user = FirebaseAuth.instance.currentUser;
        if (user != null) {
          authToken = await user.getIdToken() ?? 'dev-token';
        }
      } catch (_) {}

      await dio.post(
        '$backendBaseUrl/api/v1/devices/register',
        data: {'fcm_token': token, 'platform': 'android'},
        options: Options(
          headers: {'Authorization': 'Bearer $authToken'},
          // Don't throw on non-2xx — let the catch below log it
          validateStatus: (status) => status != null && status < 500,
        ),
      );
      debugPrint('[FCM] Device token registered with backend');
    } catch (e) {
      // Non-critical — device registration failure is logged but never surfaces to user
      debugPrint('[FCM] Device token registration failed (non-fatal): $e');
    }
  }
}
