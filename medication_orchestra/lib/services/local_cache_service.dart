import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';

/// On-device JSON cache for interactions and schedule data.
///
/// Persists the last successful API response to [SharedPreferences] so the
/// app can show data immediately on launch and continue working while offline.
///
/// Key scheme (all profile-scoped):
///   interactions_<profileId>       → JSON-encoded List
///   interactions_ts_<profileId>    → ISO-8601 timestamp of last save
///   schedule_<profileId>_<date>    → JSON-encoded Map
///   schedule_ts_<profileId>_<date> → ISO-8601 timestamp of last save
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

  /// Persists [interactions] list for [profileId].
  /// Also writes the current timestamp so callers can show "as of <time>".
  static Future<void> saveInteractions(
    String profileId,
    List<dynamic> interactions,
  ) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_interactionsKey(profileId), jsonEncode(interactions));
      await prefs.setString(
        _interactionsTsKey(profileId),
        DateTime.now().toIso8601String(),
      );
    } catch (_) {
      // Cache write failures are non-fatal — silently ignored.
    }
  }

  /// Returns the cached interactions list, or null if nothing is stored.
  static Future<List<dynamic>?> loadInteractions(String profileId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_interactionsKey(profileId));
      if (raw == null) return null;
      return jsonDecode(raw) as List<dynamic>;
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
