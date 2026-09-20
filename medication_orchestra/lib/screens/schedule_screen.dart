import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:intl/intl.dart';
import '../config/api_config.dart';
import '../services/fcm_service.dart';
import '../services/local_cache_service.dart';

/// The daily dose timetable, produced by the deterministic solver in
/// `backend/services/clinical_engine.py` (`POST /api/v1/schedule/generate`) and
/// independently verified there before it is returned.
///
/// This screen shows three things it must never overstate: the timetable, the
/// doses the solver could *not* place safely, and whether the phone will really
/// remind the user at each time.
class ScheduleScreen extends StatefulWidget {
  /// The patient to schedule for. `'all'` asks the backend for a separate,
  /// independently verified schedule per patient.
  final String profileId;

  const ScheduleScreen({super.key, required this.profileId});

  @override
  State<ScheduleScreen> createState() => _ScheduleScreenState();
}

class _ScheduleScreenState extends State<ScheduleScreen> {
  final Dio _dio = Dio();

  bool _isLoading = true;
  String? _error;
  List<Map<String, dynamic>> _doseTimes = [];
  List<String> _safetyNotes = [];
  int _interactionCount = 0;

  /// How many OS-level dose reminders were scheduled. Zero while reminders are
  /// unsupported or permission was refused, which the UI must say out loud:
  /// "your dose times are shown here" is not the same promise as "your phone
  /// will remind you".
  int _remindersScheduled = 0;

  /// Medicines the solver could not place anywhere without breaking a required
  /// gap (one entry each, however many of their doses failed), and the solver's
  /// own status for the whole schedule. A timetable that silently omits a
  /// medicine is worse than no timetable, so both are rendered.
  List<Map<String, dynamic>> _unscheduled = [];
  String _scheduleStatus = '';

  /// Timestamp of the last successful data load (local or network).
  DateTime? _cachedAt;
  /// True when the displayed data came from the local SharedPreferences cache.
  bool _loadedFromLocal = false;

  @override
  void initState() {
    super.initState();
    _generateSchedule();
  }

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  Future<void> _generateSchedule() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final pid   = widget.profileId;
    final today = DateFormat('yyyy-MM-dd').format(DateTime.now());

    // ── Step 1: Show local cache immediately (zero latency, works offline) ────
    final localSchedule = await LocalCacheService.loadSchedule(pid, today);
    final localTs       = await LocalCacheService.loadScheduleCachedAt(pid, today);
    if (localSchedule != null && mounted) {
      _applyScheduleData(localSchedule, cachedAt: localTs, fromLocal: true);
    }

    // ── Step 2: Fetch fresh schedule from backend ──────────────────────────
    try {
      final token    = await _getAuthToken();
      final response = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/schedule/generate',
        queryParameters: {'profile_id': pid},
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          receiveTimeout: const Duration(seconds: 90),
        ),
      );

      final data = response.data as Map<String, dynamic>;
      // profile_id='all' returns one schedule per patient; the caller (this
      // screen) is scoped, so take the matching one and fall back to the only
      // schedule when the household has a single patient.
      final schedules = (data['schedules'] as List<dynamic>?) ?? const [];
      final singleton = data['schedule'] as Map<String, dynamic>?;
      final schedule = singleton ??
          (schedules.isEmpty
              ? <String, dynamic>{}
              : Map<String, dynamic>.from(schedules.first as Map));

      // ── Step 3: Persist fresh schedule to local cache ──────────────────
      await LocalCacheService.saveSchedule(pid, today, schedule);

      // The reminders are scheduled from the *verified* dose times the backend
      // just produced - never from a locally invented timetable. If nothing
      // could be scheduled the user is told, instead of assuming it happened.
      await _scheduleReminders(schedule);

      if (mounted) {
        _applyScheduleData(
          schedule,
          interactionCount: (data['interaction_count'] as num?)?.toInt() ?? 0,
          cachedAt: DateTime.now(),
          fromLocal: false,
        );
      }
    } on DioException catch (e) {
      final detail = e.response?.data?['detail']?.toString() ?? '';
      if (mounted && localSchedule == null) {
        setState(() {
          _error = (detail.contains('RESOURCE_EXHAUSTED') ||
                  detail.contains('429') ||
                  e.response?.statusCode == 429)
              ? 'AI is busy generating your schedule. Please wait 30 seconds and try again.'
              : detail.isNotEmpty
                  ? detail
                  : 'Failed to generate schedule. Please try again.';
          _isLoading = false;
        });
      } else if (mounted) {
        setState(() => _isLoading = false);
      }
      debugPrint('[Schedule] Network error, using local cache: $e');
    } catch (e) {
      if (mounted && localSchedule == null) {
        setState(() {
          _error     = 'Unexpected error: $e';
          _isLoading = false;
        });
      } else if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  /// Ask the OS to remind the user at each verified dose time.
  Future<void> _scheduleReminders(Map<String, dynamic> schedule) async {
    final doseTimes = (schedule['dose_times'] as List<dynamic>?)
            ?.map((e) => Map<String, dynamic>.from(e as Map))
            .toList() ??
        const <Map<String, dynamic>>[];
    if (doseTimes.isEmpty) return;

    try {
      final count = await FcmService.scheduleDailyDoses(doseTimes);
      setState(() => _remindersScheduled = count);
    } catch (e) {
      debugPrint('[Schedule] Could not schedule reminders: $e');
      if (mounted) setState(() => _remindersScheduled = 0);
    }
  }

  /// Applies a schedule map to state. Extracted to avoid repeating the
  /// setState logic for both local-cache and network paths.
  void _applyScheduleData(
    Map<String, dynamic> schedule, {
    int interactionCount = 0,
    DateTime? cachedAt,
    bool fromLocal = false,
  }) {
    final rawDoseTimes = schedule['dose_times'] as List<dynamic>? ?? [];
    final rawNotes     = schedule['safety_notes'] as List<dynamic>? ?? [];
    final rawUnscheduled = schedule['unscheduled'] as List<dynamic>? ?? [];
    setState(() {
      _doseTimes = rawDoseTimes
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      _safetyNotes      = rawNotes.map((e) => e.toString()).toList();
      _unscheduled      = rawUnscheduled
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      _scheduleStatus   = schedule['schedule_status']?.toString() ?? '';
      _interactionCount = interactionCount;
      _cachedAt         = cachedAt;
      _loadedFromLocal  = fromLocal;
      _isLoading        = false;
    });
  }

  /// Formats a [DateTime] as "5 Jul, 2:34 PM" for the cache banner.
  String _formatCacheTime(DateTime dt) =>
      DateFormat('d MMM, h:mm a').format(dt);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Dose Schedule'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          IconButton(
            key: const Key('schedule_refresh_button'),
            icon: const Icon(Icons.refresh),
            tooltip: 'Regenerate schedule',
            onPressed: _isLoading ? null : _generateSchedule,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            CircularProgressIndicator(),
            SizedBox(height: 20),
            Padding(
              padding: EdgeInsets.symmetric(horizontal: 32),
              child: Text(
                'Building your dose schedule…\n'
                'Every dose is checked for interactions and the gaps between them.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 14, color: Colors.grey),
              ),
            ),
          ],
        ),
      );
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, size: 56, color: Colors.red),
              const SizedBox(height: 16),
              Text(
                _error!,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 14),
              ),
              const SizedBox(height: 24),
              ElevatedButton.icon(
                key: const Key('schedule_retry_button'),
                onPressed: _generateSchedule,
                icon: const Icon(Icons.refresh),
                label: const Text('Try Again'),
              ),
            ],
          ),
        ),
      );
    }

    if (_doseTimes.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.schedule, size: 56, color: Colors.grey),
              const SizedBox(height: 16),
              const Text(
                'No schedule generated yet.\nScan your medications first, then come back here.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 14, color: Colors.grey),
              ),
              const SizedBox(height: 24),
              OutlinedButton.icon(
                onPressed: _generateSchedule,
                icon: const Icon(Icons.refresh),
                label: const Text('Try Again'),
              ),
            ],
          ),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // ── Cache status banner ───────────────────────────────────────────────
        if (_cachedAt != null)
          Container(
            margin: const EdgeInsets.only(bottom: 12),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: _loadedFromLocal
                  ? Colors.orange.shade50
                  : Colors.green.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: _loadedFromLocal
                    ? Colors.orange.shade200
                    : Colors.green.shade200,
              ),
            ),
            child: Row(
              children: [
                Icon(
                  _loadedFromLocal ? Icons.wifi_off : Icons.check_circle,
                  size: 14,
                  color: _loadedFromLocal
                      ? Colors.orange.shade700
                      : Colors.green.shade700,
                ),
                const SizedBox(width: 6),
                Text(
                  _loadedFromLocal
                      ? 'Showing cached schedule from ${_formatCacheTime(_cachedAt!)}'
                      : 'Updated ${_formatCacheTime(_cachedAt!)}',
                  style: TextStyle(
                    fontSize: 12,
                    color: _loadedFromLocal
                        ? Colors.orange.shade800
                        : Colors.green.shade800,
                  ),
                ),
              ],
            ),
          ),
        // ── Solver status ─────────────────────────────────────────────────────
        // 'verified' means an independent pass re-checked every required gap;
        // 'partial' means at least one dose is in the banner above.
        if (_scheduleStatus.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              children: [
                Icon(
                  _scheduleStatus == 'verified'
                      ? Icons.verified_outlined
                      : Icons.report_problem_outlined,
                  size: 14,
                  color: _scheduleStatus == 'verified'
                      ? Colors.green.shade700
                      : Colors.orange.shade800,
                ),
                const SizedBox(width: 6),
                Text(
                  _scheduleStatus == 'verified'
                      ? 'Every dose was placed and re-checked'
                      : 'Schedule is $_scheduleStatus - see the notes above',
                  style: TextStyle(
                    fontSize: 11,
                    color: _scheduleStatus == 'verified'
                        ? Colors.green.shade800
                        : Colors.orange.shade900,
                  ),
                ),
              ],
            ),
          ),

        // ── Interaction warning banner ────────────────────────────────────────
        if (_interactionCount > 0)
          Container(
            padding: const EdgeInsets.all(12),
            margin: const EdgeInsets.only(bottom: 12),
            decoration: BoxDecoration(
              color: Colors.orange.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.orange.shade300),
            ),
            child: Row(
              children: [
                Icon(Icons.warning_amber_rounded,
                    color: Colors.orange.shade700, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '$_interactionCount interaction(s) detected. Schedule has been adjusted for safety.',
                    style: TextStyle(
                        color: Colors.orange.shade800, fontSize: 13),
                  ),
                ),
              ],
            ),
          ),

        // ── Doses the solver could not place ──────────────────────────────────
        // Rendered before anything reassuring: a medicine missing from the
        // timetable is the most important thing on this screen.
        if (_unscheduled.isNotEmpty)
          Container(
            width: double.infinity,
            key: const Key('unscheduled_banner'),
            margin: const EdgeInsets.only(bottom: 12),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: Colors.red.shade50,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.red.shade300),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.error_outline, size: 18, color: Colors.red.shade800),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${_unscheduled.length} medicine(s) could not be placed safely',
                        style: TextStyle(
                          fontWeight: FontWeight.w600,
                          color: Colors.red.shade900,
                          fontSize: 13,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                ..._unscheduled.map((entry) {
                  final name = entry['med_name']?.toString() ?? 'A medicine';
                  final reason = entry['reason']?.toString() ?? '';
                  return Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      reason == 'no_safe_slot_available'
                          ? '$name - no time of day could keep it far enough from '
                              'the other medicines. Ask your doctor or pharmacist.'
                          : '$name - $reason',
                      style: TextStyle(fontSize: 12, color: Colors.red.shade900),
                    ),
                  );
                }),
              ],
            ),
          ),

        // ── Reminder banner ───────────────────────────────────────────────────
        Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: _remindersScheduled > 0
                ? Colors.blue.shade50
                : Colors.orange.shade50,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Row(
            children: [
              Icon(
                _remindersScheduled > 0
                    ? Icons.notifications_active_outlined
                    : Icons.notifications_off_outlined,
                size: 18,
                color: _remindersScheduled > 0
                    ? Colors.blue.shade700
                    : Colors.orange.shade800,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _remindersScheduled > 0
                      ? 'Reminders set for $_remindersScheduled dose time(s) on this phone.'
                      : 'Reminders are not set on this phone - allow notifications to be '
                          'reminded at these times.',
                  style: TextStyle(
                    fontSize: 12,
                    color: _remindersScheduled > 0
                        ? Colors.blue.shade900
                        : Colors.orange.shade900,
                  ),
                ),
              ),
            ],
          ),
        ),

        // Dose time cards
        ..._doseTimes.asMap().entries.map((entry) {
          return _buildDoseTimeCard(entry.key, entry.value);
        }),

        // Safety notes
        if (_safetyNotes.isNotEmpty) ...[
          const SizedBox(height: 8),
          ExpansionTile(
            key: const Key('safety_notes_tile'),
            title: const Text(
              'Safety Notes',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
            leading:
                const Icon(Icons.info_outline, color: Color(0xFF1A73E8)),
            children: _safetyNotes
                .map((note) => Padding(
                      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('• ',
                              style: TextStyle(
                                  color: Color(0xFF1A73E8),
                                  fontWeight: FontWeight.bold)),
                          Expanded(child: Text(note)),
                        ],
                      ),
                    ))
                .toList(),
          ),
        ],

        const SizedBox(height: 16),
        const Text(
          'This schedule is built by a fixed set of clinical rules from your medication list, '
          'and independently re-checked before it is shown. Always consult your doctor before '
          'changing your medication routine.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 11, color: Colors.grey),
        ),
        const SizedBox(height: 16),
      ],
    );
  }

  Widget _buildDoseTimeCard(int index, Map<String, dynamic> doseTime) {
    final time = doseTime['time']?.toString() ?? '';
    final label = doseTime['label']?.toString() ?? 'Dose at $time';
    final meds = (doseTime['medications'] as List<dynamic>? ?? [])
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();

    return Card(
      key: Key('dose_time_card_$index'),
      margin: const EdgeInsets.only(bottom: 12),
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Time header
          Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: const BoxDecoration(
              color: Color(0xFF1A73E8),
              borderRadius:
                  BorderRadius.vertical(top: Radius.circular(12)),
            ),
            child: Row(
              children: [
                const Icon(Icons.access_time, color: Colors.white, size: 18),
                const SizedBox(width: 8),
                Text(
                  time,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 16,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.9),
                        fontSize: 13),
                  ),
                ),
              ],
            ),
          ),

          // Medication list for this time slot
          ...meds.asMap().entries.map((me) {
            final med = me.value;
            final medName = med['med_name']?.toString() ?? '';
            final dose = med['dose']?.toString() ?? '';
            final instruction = med['instruction']?.toString() ?? '';
            final warning = med['interaction_warning']?.toString();

            return Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (me.key > 0) const Divider(height: 1),
                  if (me.key > 0) const SizedBox(height: 10),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.medication,
                          size: 18, color: Color(0xFF1A73E8)),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              medName,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w600,
                                  fontSize: 14),
                            ),
                            if (dose.isNotEmpty)
                              Text(dose,
                                  style: const TextStyle(
                                      fontSize: 13,
                                      color: Colors.black87)),
                            if (instruction.isNotEmpty)
                              Text(
                                instruction,
                                style: const TextStyle(
                                    fontSize: 12, color: Colors.grey),
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  // Interaction warning chip
                  if (warning != null && warning.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Wrap(
                        children: [
                          Chip(
                            label: Text(
                              '⚠️ $warning',
                              style: const TextStyle(
                                  fontSize: 11, color: Colors.orange),
                            ),
                            backgroundColor: Colors.orange.shade50,
                            side: BorderSide(color: Colors.orange.shade300),
                            padding: EdgeInsets.zero,
                            visualDensity: VisualDensity.compact,
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            );
          }),
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}
