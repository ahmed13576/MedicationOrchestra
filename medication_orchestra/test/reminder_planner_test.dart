import 'package:flutter_test/flutter_test.dart';
import 'package:medication_orchestra/services/reminder_planner.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

/// These tests cover the rules that decide what the phone will remind the user
/// about. They are pure logic, so they run anywhere — no device, no plugins.
void main() {
  setUpAll(() {
    tz_data.initializeTimeZones();
  });

  Map<String, dynamic> slot(String time, List<String> names,
      {List<String> warnings = const []}) {
    return {
      'time': time,
      'medications': [
        for (var i = 0; i < names.length; i++)
          {
            'med_name': names[i],
            'interaction_warning': i < warnings.length ? warnings[i] : null,
          },
      ],
    };
  }

  group('planReminders', () {
    test('plans one reminder per dose time with a stable id', () {
      final planned = planReminders([
        slot('08:00', ['Warfarin 5mg']),
        slot('20:00', ['Brufen 400']),
      ]);

      expect(planned.length, 2);
      expect(planned[0].id, 1000);
      expect(planned[1].id, 1001);
      expect(planned[0].time, '08:00');
      expect(planned[1].time, '20:00');
      expect(planned[0].body, contains('Warfarin 5mg'));
    });

    test('uses the same ids for the same schedule, so regenerating replaces', () {
      final first = planReminders([slot('08:00', ['A']), slot('20:00', ['B'])]);
      final second = planReminders([slot('08:00', ['A']), slot('20:00', ['B'])]);
      expect(first.map((r) => r.id), second.map((r) => r.id));
    });

    test('skips a dose time that cannot be parsed rather than guessing', () {
      final planned = planReminders([
        slot('not-a-time', ['A']),
        slot('25:00', ['B']),
        slot('08:70', ['C']),
        slot('08:00', ['D']),
      ]);
      expect(planned.length, 1);
      expect(planned.single.body, contains('D'));
    });

    test('skips an empty slot', () {
      expect(planReminders([slot('08:00', [])]), isEmpty);
      expect(planReminders([{'time': '08:00'}]), isEmpty);
    });

    test('surfaces interaction warnings in the reminder body', () {
      final planned = planReminders([
        slot('08:00', ['Warfarin 5mg', 'Brufen 400'],
            warnings: ['Do not take with warfarin - bleeding risk']),
      ]);
      expect(planned.single.body, contains('Warfarin 5mg'));
      expect(planned.single.body, contains('bleeding risk'));
    });

    test('does not repeat the same warning twice', () {
      final planned = planReminders([
        slot('08:00', ['A', 'B'], warnings: ['same warning', 'same warning']),
      ]);
      final occurrences =
          'same warning'.allMatches(planned.single.body).length;
      expect(occurrences, 1);
    });

    test('never produces duplicate notification ids', () {
      final planned = planReminders([
        slot('08:00', ['A']),
        slot('08:00', ['B']),
        slot('08:00', ['C']),
      ]);
      final ids = planned.map((r) => r.id).toSet();
      expect(ids.length, planned.length);
    });
  });

  group('nextOccurrence', () {
    test('schedules later today when the time has not passed', () {
      final kolkata = tz.getLocation('Asia/Kolkata');
      final now = tz.TZDateTime(kolkata, 2026, 9, 18, 6, 30);
      final next = nextOccurrence(8, 0, kolkata, now: now);
      expect(next.day, 18);
      expect(next.hour, 8);
      expect(next.minute, 0);
    });

    test('rolls over to tomorrow when the time has passed', () {
      final kolkata = tz.getLocation('Asia/Kolkata');
      final now = tz.TZDateTime(kolkata, 2026, 9, 18, 21, 0);
      final next = nextOccurrence(8, 0, kolkata, now: now);
      expect(next.day, 19);
      expect(next.hour, 8);
    });

    test('rolls over when the time is exactly now', () {
      final kolkata = tz.getLocation('Asia/Kolkata');
      final now = tz.TZDateTime(kolkata, 2026, 9, 18, 8, 0);
      final next = nextOccurrence(8, 0, kolkata, now: now);
      expect(next.day, 19);
    });

    test('is always in the future', () {
      final kolkata = tz.getLocation('Asia/Kolkata');
      final now = tz.TZDateTime.now(kolkata);
      for (final hour in [0, 6, 12, 18, 23]) {
        expect(nextOccurrence(hour, 15, kolkata, now: now).isAfter(now), isTrue);
      }
    });
  });
}
