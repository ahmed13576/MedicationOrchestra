import 'dart:io';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

import 'reminder_planner.dart';

/// Top-level background message handler — MUST be a top-level function.
/// Called by FCM when the app is in background/terminated.
@pragma('vm:entry-point')
Future<void> _firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  debugPrint('[FCM] Background message: ${message.messageId}');
}

/// Manages Firebase Cloud Messaging setup, notification channels, device token
/// registration, foreground display, and the *scheduled dose reminders*.
///
/// The reminders are local notifications scheduled by the operating system.
/// That design is deliberate: a caregiver's phone must remind them at 08:00
/// whether or not there is a network, whether or not the app is running, and
/// without depending on a server-side cron job that does not exist.
class FcmService {
  static final FlutterLocalNotificationsPlugin _localNotifications =
      FlutterLocalNotificationsPlugin();

  /// Dose reminders are scheduled with ids at or above this base so they can be
  /// cancelled as a group without touching any other notification.
  static const int reminderIdBase = 1000;

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

  /// Set by the app so a notification tap can route to a screen.
  static void Function(String payload)? onNotificationTap;

  static bool _timezonesReady = false;

  /// Load the timezone database and pin it to the device's zone.
  ///
  /// `zonedSchedule` works in wall-clock time, so "08:00" has to mean 08:00
  /// where the user actually is. If the platform will not report a zone name we
  /// fall back to UTC and log it, which is a wrong hour rather than no reminder.
  static Future<void> _initialiseTimezones() async {
    if (_timezonesReady) return;
    tz_data.initializeTimeZones();
    try {
      // flutter_timezone >= 4 returns a TimezoneInfo; the zone name it carries
      // is what the timezone database is keyed on.
      final zone = await FlutterTimezone.getLocalTimezone();
      tz.setLocalLocation(tz.getLocation(zone.identifier));
    } catch (e) {
      debugPrint('[FCM] Could not resolve the device timezone ($e); using UTC');
    }
    _timezonesReady = true;
  }

  /// Initialize FCM, request permissions, create notification channels,
  /// resolve the timezone and register the device token with the backend.
  ///
  /// Call this fire-and-forget from HomeScreen.initState().
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

      // 4. Initialize flutter_local_notifications (foreground + scheduled).
      const initSettings = InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
        iOS: DarwinInitializationSettings(
          requestAlertPermission: false,
          requestBadgePermission: false,
          requestSoundPermission: false,
        ),
      );
      await _localNotifications.initialize(
        initSettings,
        onDidReceiveNotificationResponse: (response) {
          final payload = response.payload;
          if (payload != null && onNotificationTap != null) {
            onNotificationTap!(payload);
          }
        },
      );
      await _initialiseTimezones();

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
          payload: isSOSAlert ? 'sos_alert' : 'dose_reminder',
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

  // ── Dose reminders ────────────────────────────────────────────────────────

  /// Schedule one repeating daily reminder per dose time.
  ///
  /// [doseTimes] is the `dose_times` array returned by
  /// `POST /api/v1/schedule/generate`. Returns how many were scheduled; the
  /// caller must surface "reminders are off" when this is zero, rather than
  /// letting the caregiver believe they will be reminded.
  ///
  /// Planning lives in reminder_planner.dart so it can be unit-tested without a
  /// device; this method only talks to the operating system.
  static Future<int> scheduleDailyDoses(
    List<Map<String, dynamic>> doseTimes, {
    int idBase = reminderIdBase,
  }) async {
    await _initialiseTimezones();

    if (Platform.isAndroid) {
      final androidPlugin = _localNotifications
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>();
      await androidPlugin?.requestNotificationsPermission();
    }

    // Replace the previous schedule instead of stacking onto it: the medicine
    // list changes, and two conflicting reminders for the same hour is worse
    // than none at all.
    await cancelDoseReminders(idBase: idBase);

    final planned = planReminders(doseTimes, idBase: idBase);
    var scheduled = 0;
    for (final reminder in planned) {
      await _localNotifications.zonedSchedule(
        reminder.id,
        'Time for your medicines',
        reminder.body,
        nextOccurrence(reminder.hour, reminder.minute, tz.local),
        const NotificationDetails(
          android: AndroidNotificationDetails(
            'medication_reminders',
            'Medication Reminders',
            channelDescription: 'Daily dose reminder notifications',
            importance: Importance.high,
            priority: Priority.high,
            icon: '@mipmap/ic_launcher',
            styleInformation: BigTextStyleInformation(''),
          ),
          iOS: DarwinNotificationDetails(),
        ),
        // Inexact alarms need no special "alarms & reminders" permission and are
        // accurate to a few minutes, which is right for a dose reminder and far
        // more reliable across Android OEM battery savers than exact alarms that
        // get killed anyway.
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
        uiLocalNotificationDateInterpretation:
            UILocalNotificationDateInterpretation.absoluteTime,
        matchDateTimeComponents: DateTimeComponents.time, // repeat daily
        payload: 'dose_reminder:${reminder.time}',
      );
      scheduled++;
    }
    debugPrint('[FCM] Scheduled $scheduled dose reminder(s)');
    return scheduled;
  }

  /// Cancel every scheduled dose reminder.
  static Future<int> cancelDoseReminders({int idBase = reminderIdBase}) async {
    final pending = await _localNotifications.pendingNotificationRequests();
    var cancelled = 0;
    for (final request in pending) {
      if (request.id >= idBase) {
        await _localNotifications.cancel(request.id);
        cancelled++;
      }
    }
    return cancelled;
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
