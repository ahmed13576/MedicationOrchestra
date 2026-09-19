import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

/// On-device JSON cache for interactions and schedule data.
///
/// Persists the last successful API response to [SharedPreferences] so the
/// app can show data immediately on launch and continue working while offline.
///
/// Key scheme (all profile-scoped):
///   interactions_<profileId>       → JSON-encoded envelope (see below)
///   interactions_ts_<profileId>    → ISO-8601 timestamp of last save
///   schedule_<profileId>_<date>    → JSON-encoded Map
///   schedule_ts_<profileId>_<date> → ISO-8601 timestamp of last save
///
/// The interactions envelope is
///   {"interactions": [...], "coverage": {...}, "unchecked": [...],
///    "review_status": "…"}
/// because the coverage ledger and the unchecked list are part of the answer:
/// caching the alert list alone made an offline screen unable to say *what* it
/// had not checked, and a reader cannot tell "nothing found" from "not looked
/// at". Older caches that stored a bare list are still read, and are treated as
/// incomplete coverage rather than as a clean bill of health.
///
/// All methods are static and async. Every call is wrapped in try/catch —
/// a cache failure will never crash the app; it simply returns null.
class LocalCacheService {
  // ── Key helpers ──────────────────────────────────────────────────────────────

  static String _interactionsKey(String profileId) =>
      'interactions_$profileId';

  static String _interactionsTsKey(String profileId) =>
      'interactions_ts_$profileId';

  static String _scheduleKey(String profileId, String date) =>
      'schedule_${profileId}_$date';

  static String _scheduleTsKey(String profileId, String date) =>
      'schedule_ts_${profileId}_$date';

  // ── Interactions ─────────────────────────────────────────────────────────────

  /// Persists an interactions response for [profileId].
  ///
  /// [coverage], [unchecked] and [reviewStatus] are stored alongside the alert
  /// list so that the offline screen can still say what was not checked. Also
  /// writes the current timestamp so callers can show "as of <time>".
  static Future<void> saveInteractions(
    String profileId,
    List<dynamic> interactions, {
    Map<String, dynamic>? coverage,
    List<dynamic>? unchecked,
    String? reviewStatus,
  }) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final envelope = <String, dynamic>{
        'interactions': interactions,
        'coverage': coverage ?? <String, dynamic>{},
        'unchecked': unchecked ?? <dynamic>[],
        'review_status': reviewStatus ?? '',
      };
      await prefs.setString(_interactionsKey(profileId), jsonEncode(envelope));
      await prefs.setString(
        _interactionsTsKey(profileId),
        DateTime.now().toIso8601String(),
      );
    } catch (_) {
      // Cache write failures are non-fatal — silently ignored.
    }
  }

  /// Returns the cached interactions envelope, or null if nothing is stored.
  ///
  /// A legacy bare-list cache is wrapped in an envelope with empty coverage, so
  /// callers never mistake "we did not store the coverage" for "all checked".
  static Future<Map<String, dynamic>?> loadInteractions(String profileId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_interactionsKey(profileId));
      if (raw == null) return null;
      final decoded = jsonDecode(raw);
      if (decoded is List) {
        return <String, dynamic>{
          'interactions': decoded,
          'coverage': <String, dynamic>{},
          'unchecked': <dynamic>[],
          'review_status': '',
        };
      }
      return Map<String, dynamic>.from(decoded as Map);
    } catch (_) {
      return null;
    }
  }

  /// Returns when interactions were last saved, or null if never cached.
  static Future<DateTime?> loadInteractionsCachedAt(String profileId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final ts = prefs.getString(_interactionsTsKey(profileId));
      if (ts == null) return null;
      return DateTime.tryParse(ts);
    } catch (_) {
      return null;
    }
  }

  /// Removes cached interactions for [profileId] (e.g. after profile switch).
  static Future<void> clearInteractions(String profileId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_interactionsKey(profileId));
      await prefs.remove(_interactionsTsKey(profileId));
    } catch (_) {}
  }

  // ── Schedule ──────────────────────────────────────────────────────────────────

  /// Persists [schedule] map for [profileId] on [date] (ISO-8601 date string).
  static Future<void> saveSchedule(
    String profileId,
    String date,
    Map<String, dynamic> schedule,
  ) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_scheduleKey(profileId, date), jsonEncode(schedule));
      await prefs.setString(
        _scheduleTsKey(profileId, date),
        DateTime.now().toIso8601String(),
      );
    } catch (_) {}
  }

  /// Returns the cached schedule for [profileId] on [date], or null if missing.
  static Future<Map<String, dynamic>?> loadSchedule(
    String profileId,
    String date,
  ) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_scheduleKey(profileId, date));
      if (raw == null) return null;
      return Map<String, dynamic>.from(jsonDecode(raw) as Map);
    } catch (_) {
      return null;
    }
  }

  /// Returns when the schedule for [profileId] on [date] was last saved.
  static Future<DateTime?> loadScheduleCachedAt(
    String profileId,
    String date,
  ) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final ts = prefs.getString(_scheduleTsKey(profileId, date));
      if (ts == null) return null;
      return DateTime.tryParse(ts);
    } catch (_) {
      return null;
    }
  }
}
