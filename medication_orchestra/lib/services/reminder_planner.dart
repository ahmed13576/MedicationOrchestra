/// Pure planning logic for dose reminders.
///
/// Kept separate from FcmService (and free of Flutter plugin calls) so the rules
/// that decide *what* gets scheduled can be unit-tested without a device: which
/// dose times are usable, what the next occurrence is, and which reminder ids a
/// re-generation must replace.
library;

import 'package:timezone/timezone.dart' as tz;

/// A validated reminder: a wall-clock time plus the medicines due at it.
class PlannedReminder {
  /// Notification id. Deterministic per slot, so regenerating the schedule
  /// replaces that slot's reminder instead of adding a duplicate.
  final int id;
  final int hour;
  final int minute;
  final String body;

  const PlannedReminder({
    required this.id,
    required this.hour,
    required this.minute,
    required this.body,
  });

  String get time =>
      '${hour.toString().padLeft(2, '0')}:${minute.toString().padLeft(2, '0')}';
}

/// Turn the backend's verified `dose_times` into the reminders to schedule.
///
/// Anything malformed is skipped rather than guessed at: a reminder at the wrong
/// time is a medication error, not a cosmetic bug.
List<PlannedReminder> planReminders(
  List<Map<String, dynamic>> doseTimes, {
  int idBase = 1000,
}) {
  final planned = <PlannedReminder>[];
  final usedIds = <int>{};

  for (var index = 0; index < doseTimes.length; index++) {
    final slot = doseTimes[index];
    final parts = ((slot['time'] as String?) ?? '').split(':');
    if (parts.length != 2) continue;

    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) continue;
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) continue;

    final medications = (slot['medications'] as List<dynamic>?) ?? const [];
    if (medications.isEmpty) continue;

    final names = medications
        .map((m) => (m is Map ? m['med_name']?.toString() ?? '' : ''))
        .where((n) => n.isNotEmpty)
        .toList();
    if (names.isEmpty) continue;

    final warnings = <String>{
      for (final m in medications)
        if (m is Map && (m['interaction_warning']?.toString() ?? '').isNotEmpty)
          m['interaction_warning'].toString(),
    }.toList();

    final body = warnings.isEmpty
        ? names.join(', ')
        : '${names.join(', ')}\n\u26A0\uFE0F ${warnings.join('\n')}';

    var id = idBase + index;
    while (usedIds.contains(id)) {
      id++;
    }
    usedIds.add(id);

    planned.add(PlannedReminder(id: id, hour: hour, minute: minute, body: body));
  }

  return planned;
}

/// The next occurrence of [hour]:[minute] in [location], today if it is still
/// ahead, otherwise tomorrow.
tz.TZDateTime nextOccurrence(
  int hour,
  int minute,
  tz.Location location, {
  tz.TZDateTime? now,
}) {
  final current = now ?? tz.TZDateTime.now(location);
  var scheduled =
      tz.TZDateTime(location, current.year, current.month, current.day, hour, minute);
  if (!scheduled.isAfter(current)) {
    scheduled = scheduled.add(const Duration(days: 1));
  }
  return scheduled;
}
