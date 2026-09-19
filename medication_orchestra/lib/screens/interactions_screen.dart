import 'package:flutter/foundation.dart'; // for compute()
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:intl/intl.dart';
import '../config/api_config.dart';
import '../services/local_cache_service.dart';

/// Parses the raw interactions API list on a background isolate.
/// MUST be a top-level function — compute() cannot use closures or instance methods.
List<Map<String, dynamic>> _parseInteractionsList(dynamic rawList) {
  final list = rawList as List<dynamic>? ?? [];
  return list
      .map((e) => Map<String, dynamic>.from(e as Map))
      .toList();
}

/// Displays drug-drug interaction alerts for the household's active medications.
///
/// Each alert shows:
///  - Severity badge (color-coded)
///  - Explanation (collapsible)
///  - What to do (action steps)
///  - Time gap recommendation (blue info box, when applicable)
///  - Interaction symptoms to watch for (red caution box, for major/contraindicated)
///  - Acknowledge button
class InteractionsScreen extends StatefulWidget {
  /// The active profile ID, or 'all' for the household.
  ///
  /// This is sent to the backend: 'all' checks every patient *separately* so
  /// two people's medicines are never combined, and a specific id checks that
  /// patient alone.
  final String profileId;

  const InteractionsScreen({
    super.key,
    this.profileId = 'default',
  });

  @override
  State<InteractionsScreen> createState() => _InteractionsScreenState();
}

class _InteractionsScreenState extends State<InteractionsScreen> {
  final Dio _dio = Dio();

  bool _isLoading = true;
  String? _error;
  List<Map<String, dynamic>> _interactions = [];

  /// Medicines the engine could not identify. Non-empty means the list below is
  /// NOT a complete check - the UI must say so instead of showing a green tick.
  List<Map<String, dynamic>> _unchecked = [];

  /// Coverage ledger from the backend: how many medicines were actually checked.
  Map<String, dynamic> _coverage = {};

  /// The clinical review state of the knowledge base that produced this answer.
  String _reviewStatus = '';

  bool get _coverageIsComplete => _coverage['is_complete'] == true;

  /// Timestamp of the last successful data load (local or network).
  DateTime? _cachedAt;
  /// True when the displayed data came from the local SharedPreferences cache
  /// rather than a fresh network response.
  bool _loadedFromLocal = false;

  @override
  void initState() {
    super.initState();
    _fetchInteractions();
  }

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token'; // DEV_MODE fallback
  }

  Future<void> _fetchInteractions() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final pid = widget.profileId;

    // ── Step 1: Show local cache immediately (zero latency, works offline) ────
    // The cache holds the coverage ledger and the unchecked list too, so the
    // offline screen can still say what was and was not checked. A cache with
    // no coverage block renders as "not fully checked", never as a green tick.
    final localEnvelope = await LocalCacheService.loadInteractions(pid);
    final localTs       = await LocalCacheService.loadInteractionsCachedAt(pid);
    if (localEnvelope != null && mounted) {
      final parsed = await compute(
        _parseInteractionsList,
        localEnvelope['interactions'] as List<dynamic>? ?? <dynamic>[],
      );
      setState(() {
        _interactions    = parsed;
        _coverage        = (localEnvelope['coverage'] as Map?)?.cast<String, dynamic>() ?? {};
        _unchecked       = (localEnvelope['unchecked'] as List?)
                ?.map((e) => Map<String, dynamic>.from(e as Map))
                .toList() ??
            <Map<String, dynamic>>[];
        _reviewStatus    = (localEnvelope['review_status'] as String?) ?? '';
        _cachedAt        = localTs;
        _loadedFromLocal = true;
        _isLoading       = false;
      });
    }

    // ── Step 2: Fetch fresh data from backend ─────────────────────────────────
    try {
      final token = await _getAuthToken();
      // profileId scopes the check to one patient. 'all' asks the backend to
      // check each patient separately (never mixing two people's medicines).
      final response = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/interactions',
        queryParameters: {'profile_id': pid},
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          receiveTimeout: const Duration(seconds: 60),
        ),
      );

      final data    = response.data as Map<String, dynamic>;
      final rawList = data['interactions'];
      final parsed  = await compute(_parseInteractionsList, rawList);
      final coverage = (data['coverage'] as Map?)?.cast<String, dynamic>() ?? {};
      final unchecked = (data['unchecked'] as List?)
              ?.map((e) => Map<String, dynamic>.from(e as Map))
              .toList() ??
          <Map<String, dynamic>>[];

      // ── Step 3: Persist fresh data to local cache (with the ledger) ───────
      await LocalCacheService.saveInteractions(
        pid,
        rawList as List<dynamic>? ?? [],
        coverage: coverage,
        unchecked: unchecked,
        reviewStatus: data['review_status'] as String?,
      );

      if (mounted) {
        setState(() {
          _interactions    = parsed;
          _unchecked       = unchecked;
          _coverage        = coverage;
          _reviewStatus    = (data['review_status'] as String?) ?? '';
          _cachedAt        = DateTime.now();
          _loadedFromLocal = false;
          _isLoading       = false;
        });
      }
    } on DioException catch (e) {
      // Network failed — keep local cache visible if we already showed it.
      final detail = e.response?.data?['detail']?.toString() ?? '';
      if (mounted && localEnvelope == null) {
        // No local cache either — show error.
        setState(() {
          _error = (detail.contains('RESOURCE_EXHAUSTED') || detail.contains('429'))
              ? 'The AI is busy checking your medications. Please wait 30 seconds and try again.'
              : detail.isNotEmpty
                  ? detail
                  : 'Failed to check interactions. Please try again.';
          _isLoading = false;
        });
      } else if (mounted) {
        // Local data is already displayed — just stop the loading indicator.
        setState(() => _isLoading = false);
      }
      debugPrint('[Interactions] Network error, using local cache: $e');
    } catch (e) {
      if (mounted && localEnvelope == null) {
        setState(() {
          _error     = 'Unexpected error: $e';
          _isLoading = false;
        });
      } else if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  /// Formats a [DateTime] as "5 Jul, 2:34 PM" for the cache banner.
  String _formatCacheTime(DateTime dt) =>
      DateFormat('d MMM, h:mm a').format(dt);

  Future<void> _acknowledgeInteraction(String interactionId, int index) async {
    try {
      final token = await _getAuthToken();
      final response = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/interactions/$interactionId/acknowledge',
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}');
      }
      setState(() {
        _interactions[index]['acknowledged'] = true;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Alert acknowledged'),
            backgroundColor: Colors.green,
            duration: Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to acknowledge: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  // ── Severity styling ──────────────────────────────────────────────────────

  Color _severityColor(String severity) {
    switch (severity) {
      case 'contraindicated':
        return Colors.red.shade700;
      case 'major':
        return Colors.deepOrange;
      case 'moderate':
        return Colors.amber.shade700;
      default:
        return Colors.grey.shade600;
    }
  }

  Color _severityTextColor(String severity) {
    return severity == 'moderate' ? Colors.black87 : Colors.white;
  }

  String _severityLabel(String severity) {
    switch (severity) {
      case 'contraindicated':
        return '⛔ Contraindicated';
      case 'major':
        return '⚠️ Major';
      case 'moderate':
        return '⚡ Moderate';
      default:
        return 'ℹ️ Minor';
    }
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey.shade50,
      appBar: AppBar(
        title: const Text(
          'Interaction Alerts',
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        backgroundColor: Colors.white,
        foregroundColor: Colors.black87,
        elevation: 1,
        actions: [
          if (!_isLoading)
            IconButton(
              key: const Key('interactions_refresh_button'),
              icon: const Icon(Icons.refresh),
              tooltip: 'Re-check interactions',
              onPressed: _fetchInteractions,
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
            SizedBox(height: 16),
            Text(
              'Checking your medications for interactions…',
              style: TextStyle(color: Colors.grey),
              textAlign: TextAlign.center,
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
              const Icon(Icons.error_outline, color: Colors.red, size: 48),
              const SizedBox(height: 12),
              Text(
                _error!,
                style: const TextStyle(color: Colors.red),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 20),
              ElevatedButton.icon(
                key: const Key('interactions_retry_button'),
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
                onPressed: _fetchInteractions,
              ),
            ],
          ),
        ),
      );
    }

    if (_interactions.isEmpty) {
      return _buildEmptyState();
    }

    return Column(
      children: [
        // ── Cache status banner ───────────────────────────────────────────────
        if (_cachedAt != null)
          Container(
            width: double.infinity,
            color: _loadedFromLocal
                ? Colors.orange.shade50
                : Colors.green.shade50,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Row(
              children: [
                // Deliberately not a tick: the tick on this screen means "every
                // medicine was checked", and a freshness badge must not borrow it.
                Icon(
                  _loadedFromLocal ? Icons.wifi_off : Icons.cloud_done_outlined,
                  size: 14,
                  color: _loadedFromLocal
                      ? Colors.orange.shade700
                      : Colors.green.shade700,
                ),
                const SizedBox(width: 6),
                Text(
                  _loadedFromLocal
                      ? 'Showing cached data from ${_formatCacheTime(_cachedAt!)}'
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
        _buildCoverageBanner(),
        // ── Interaction count banner ──────────────────────────────────────────
        Container(
          width: double.infinity,
          color: Colors.orange.shade50,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Text(
            '${_interactions.length} interaction(s) found — review before taking medications',
            style: TextStyle(
              color: Colors.orange.shade900,
              fontWeight: FontWeight.w600,
            ),
            textAlign: TextAlign.center,
          ),
        ),
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.all(12),
            itemCount: _interactions.length,
            itemBuilder: (ctx, i) => _buildInteractionCard(_interactions[i], i),
          ),
        ),
      ],
    );
  }


  /// The empty state.
  ///
  /// This used to be a green tick and "safe to take together" — including when
  /// the backend had no medicines to check, no information about one of them,
  /// or no clinical data at all. Reassurance we have not earned is the one
  /// defect this product cannot ship, so the tick now requires complete
  /// coverage, and anything less gets an amber "not fully checked" panel that
  /// names the medicines we could not identify.
  Widget _buildEmptyState() {
    final somethingToSay = _coverage.isNotEmpty;
    final noMedications = somethingToSay && (_coverage['medications_total'] ?? 0) == 0;

    if (_coverageIsComplete && _unchecked.isEmpty && !noMedications) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: Colors.green.shade50,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  Icons.verified_outlined,
                  color: Colors.green.shade600,
                  size: 56,
                ),
              ),
              const SizedBox(height: 20),
              Text(
                'No dangerous combinations found',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                  color: Colors.green.shade700,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                'All ${_coverage['medications_total']} of your medicines were checked '
                'against ${_coverage['knowledge_base_rules'] ?? 'the'} known interaction rules.',
                style: TextStyle(color: Colors.grey.shade700),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              _buildReviewDisclaimer(),
            ],
          ),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const SizedBox(height: 24),
        Icon(
          noMedications ? Icons.medication_outlined : Icons.error_outline,
          color: noMedications ? Colors.blueGrey : Colors.orange.shade800,
          size: 56,
        ),
        const SizedBox(height: 16),
        Text(
          noMedications
              ? 'No medicines to check yet'
              : 'Some medicines could not be checked',
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 8),
        Text(
          noMedications
              ? 'Scan a prescription or add your medicines, and we will check them '
                  'against each other.'
              : 'We are not able to say these are safe to take together yet.',
          style: TextStyle(color: Colors.grey.shade700),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 20),
        if (_unchecked.isNotEmpty) ...[
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Colors.orange.shade50,
              border: Border.all(color: Colors.orange.shade200),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Not checked:',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    color: Colors.orange.shade900,
                  ),
                ),
                const SizedBox(height: 8),
                ..._unchecked.map((item) => Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(
                        '• ${item['brand_name'] ?? 'Unknown medicine'} — '
                        '${item['reason'] ?? 'we could not identify it'}',
                        style: TextStyle(color: Colors.orange.shade900),
                      ),
                    )),
              ],
            ),
          ),
          const SizedBox(height: 16),
        ] else if (_coverage.isNotEmpty) ...[
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Colors.orange.shade50,
              border: Border.all(color: Colors.orange.shade200),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(
              'Checked ${_coverage['medications_fully_checked']} of '
              '${_coverage['medications_total']} medicines.',
              style: TextStyle(color: Colors.orange.shade900),
            ),
          ),
          const SizedBox(height: 16),
        ],
        _buildReviewDisclaimer(),
      ],
    );
  }

  /// The knowledge base shipped today is a demonstration set that has not been
  /// reviewed by a clinician. Hiding that would be the more damaging choice.
  Widget _buildReviewDisclaimer() {
    if (!_reviewStatus.toLowerCase().contains('not clinician-reviewed')) {
      return const SizedBox.shrink();
    }
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Colors.blueGrey.shade50,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.science_outlined, size: 16, color: Colors.blueGrey.shade700),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Reference build: the medicine rules used here have not yet been '
              'reviewed by a clinical pharmacist. Always confirm with your doctor '
              'or pharmacist before changing any medicine.',
              style: TextStyle(fontSize: 11, color: Colors.blueGrey.shade800),
            ),
          ),
        ],
      ),
    );
  }

  /// Coverage banner shown above the findings list when the list is non-empty.
  Widget _buildCoverageBanner() {
    final total = _coverage['medications_total'];
    if (total == null) return const SizedBox.shrink();
    final uncheckedCount = _coverage['medications_unchecked'] ?? _unchecked.length;

    if (_coverageIsComplete) {
      return Container(
        width: double.infinity,
        color: Colors.green.shade50,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Text(
          'All $total medicines checked',
          style: TextStyle(fontSize: 12, color: Colors.green.shade800),
          textAlign: TextAlign.center,
        ),
      );
    }

    return Container(
      width: double.infinity,
      color: Colors.orange.shade100,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Text(
        '$uncheckedCount of $total medicines could not be checked — '
        'the findings below are not the whole picture.',
        style: TextStyle(
          fontSize: 12,
          color: Colors.orange.shade900,
          fontWeight: FontWeight.w600,
        ),
        textAlign: TextAlign.center,
      ),
    );
  }

  Widget _buildInteractionCard(Map<String, dynamic> interaction, int index) {
    final severity = (interaction['severity'] as String?) ?? 'moderate';
    final title = (interaction['title'] as String?) ?? 'Drug Interaction';
    final explanation = (interaction['explanation'] as String?) ?? '';
    final whatToDo = (interaction['what_to_do'] as String?) ?? '';
    final timeGapHours = (interaction['time_gap_hours'] as num?)?.toInt() ?? 0;
    final timeGapNote = (interaction['time_gap_note'] as String?) ?? '';
    final emergencyNote = (interaction['emergency_note'] as String?);
    final safeAlternative = (interaction['safe_alternative'] as String?);
    final acknowledged = interaction['acknowledged'] as bool? ?? false;
    final medAName = (interaction['med_a_name'] as String?) ?? '';
    final medBName = (interaction['med_b_name'] as String?) ?? '';

    // Use the pair as a stable id for acknowledge calls
    final interactionId =
        '${interaction['med_a_id'] ?? medAName}_${interaction['med_b_id'] ?? medBName}';

    final severityColor = _severityColor(severity);

    return Card(
      key: Key('interaction_card_$index'),
      margin: const EdgeInsets.only(bottom: 12),
      elevation: 2,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: severityColor.withValues(alpha: 0.4), width: 1),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Header: severity badge + drug names ──────────────────────────
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: severityColor.withValues(alpha: 0.08),
              borderRadius:
                  const BorderRadius.vertical(top: Radius.circular(12)),
            ),
            child: Row(
              children: [
                Chip(
                  label: Text(
                    _severityLabel(severity),
                    style: TextStyle(
                      color: _severityTextColor(severity),
                      fontWeight: FontWeight.bold,
                      fontSize: 11,
                    ),
                  ),
                  backgroundColor: severityColor,
                  padding: EdgeInsets.zero,
                  visualDensity: VisualDensity.compact,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '$medAName + $medBName',
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 14,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (acknowledged)
                  Icon(Icons.check_circle,
                      color: Colors.green.shade400, size: 18),
              ],
            ),
          ),

          // ── Title + Explanation (collapsible) ────────────────────────────
          ExpansionTile(
            key: Key('interaction_expand_$index'),
            title: Text(
              title,
              style: const TextStyle(
                fontWeight: FontWeight.w600,
                fontSize: 14,
              ),
            ),
            initiallyExpanded: severity == 'contraindicated' || severity == 'major',
            children: [
              if (explanation.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                  child: Text(
                    explanation,
                    style: const TextStyle(height: 1.5),
                  ),
                ),

              // ── What to do ─────────────────────────────────────────────
              if (whatToDo.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'What to do:',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          color: Colors.black87,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(whatToDo, style: const TextStyle(height: 1.5)),
                    ],
                  ),
                ),

              // ── Safe Alternative ────────────────────────────────────────
              if (safeAlternative != null && safeAlternative.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                  child: Row(
                    children: [
                      const Icon(Icons.swap_horiz,
                          size: 16, color: Colors.green),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'Safe alternative: $safeAlternative',
                          style: const TextStyle(color: Colors.green),
                        ),
                      ),
                    ],
                  ),
                ),

              // ── Time Gap Section ────────────────────────────────────────
              if (timeGapHours > 0 || timeGapNote.isNotEmpty)
                _buildInfoBox(
                  key: Key('interaction_timegap_$index'),
                  icon: Icons.schedule,
                  color: Colors.blue,
                  title: timeGapHours > 0
                      ? 'Suggested Time Gap: $timeGapHours hour${timeGapHours > 1 ? "s" : ""}'
                      : 'Timing Note',
                  body: timeGapNote.isNotEmpty
                      ? timeGapNote
                      : 'Allow at least $timeGapHours hour${timeGapHours > 1 ? "s" : ""} between these medications.',
                ),

              // ── Emergency / Symptoms Section ────────────────────────────
              if (emergencyNote != null && emergencyNote.isNotEmpty)
                _buildInfoBox(
                  key: Key('interaction_emergency_$index'),
                  icon: Icons.local_hospital_rounded,
                  color: Colors.red.shade700,
                  title: 'Watch for these symptoms',
                  body: emergencyNote,
                  footer: 'Call 112 (India) in an emergency',
                ),

              const SizedBox(height: 8),
            ],
          ),

          // ── Acknowledge button ───────────────────────────────────────────
          if (!acknowledged)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
              child: OutlinedButton.icon(
                key: Key('interaction_acknowledge_$index'),
                icon: const Icon(Icons.check, size: 16),
                label: const Text('Acknowledge'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: Colors.red.shade700,
                  side: BorderSide(color: Colors.red.shade700),
                ),
                onPressed: () => _acknowledgeInteraction(interactionId, index),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildInfoBox({
    required Key key,
    required IconData icon,
    required Color color,
    required String title,
    required String body,
    String? footer,
  }) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
      child: Container(
        key: key,
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.3)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 16, color: color),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    title,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      color: color,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(body, style: const TextStyle(height: 1.5)),
            if (footer != null) ...[
              const SizedBox(height: 6),
              Text(
                footer,
                style: TextStyle(
                  color: color,
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
