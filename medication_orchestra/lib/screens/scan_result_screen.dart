import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../config/api_config.dart';

class ScanResultScreen extends StatefulWidget {
  final List<Map<String, dynamic>> medications;
  final String profileId;

  /// 'prescription' | 'blister_pack'
  final String detectedType;

  /// True when backend flagged ≥1 medication with confidence < 80
  final bool requiresReview;

  const ScanResultScreen({
    required this.medications,
    required this.profileId,
    this.detectedType = 'prescription',
    this.requiresReview = false,
    super.key,
  });

  @override
  State<ScanResultScreen> createState() => _ScanResultScreenState();
}

class _ScanResultScreenState extends State<ScanResultScreen> {
  late List<Map<String, dynamic>> _medications;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _medications = List.from(widget.medications.map((m) => Map<String, dynamic>.from(m)));
  }

  bool get _isBlister => widget.detectedType == 'blister_pack';

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  Future<void> _confirmMedications() async {
    setState(() => _isSaving = true);
    try {
      final token = await _getAuthToken();
      await Dio().post(
        '${ApiConfig.baseUrl}/api/v1/medications/confirm',
        data: {
          'profile_id': widget.profileId,
          'medications': _medications,
        },
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            '✅ ${_medications.length} medication${_medications.length == 1 ? '' : 's'} saved successfully!',
          ),
          backgroundColor: Colors.green.shade700,
          behavior: SnackBarBehavior.floating,
        ),
      );
      Navigator.pop(context);
    } on DioException catch (e) {
      if (!mounted) return;
      final msg = e.response?.data?['detail'] ?? 'Failed to save. Please try again.';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg.toString()), backgroundColor: Colors.red.shade700),
      );
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  void _showEditDialog(int index) async {
    final med = Map<String, dynamic>.from(_medications[index]);
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => _MedicationEditDialog(
        medication: med,
        isBlister: _isBlister,
      ),
    );

    if (result != null) {
      setState(() {
        _medications[index]['brand_name'] = result['brand_name'];
        _medications[index]['dosage'] = result['dosage'];
        _medications[index]['frequency_english'] = result['frequency_english'];
        _medications[index]['timing'] = result['timing'];
        if (_isBlister) {
          _medications[index]['expiry_date'] = result['expiry_date'];
        }
      });
    }
  }

  void _showAddManualDialog() async {
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => _MedicationEditDialog(
        medication: const {},
        isBlister: _isBlister,
      ),
    );

    if (result != null) {
      setState(() {
        _medications.add({
          'brand_name': result['brand_name'],
          'generic_name': result['brand_name'],
          'dosage': result['dosage'],
          'frequency_english': result['frequency_english'],
          'timing': result['timing'],
          if (_isBlister) 'expiry_date': result['expiry_date'],
          'source_type': 'manual',
          'confidence': 'high',
          'notes': 'Added manually',
        });
      });
    }
  }

  Widget _buildPrescriptionCard(Map<String, dynamic> med, int index) {
    final confidence = med['confidence'] ?? 'high';
    final timings = (med['timing'] as List?)?.cast<String>() ?? [];

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(med['brand_name'] ?? '',
                          style: const TextStyle(
                              fontSize: 16, fontWeight: FontWeight.bold)),
                      if ((med['generic_name'] ?? '').isNotEmpty &&
                          med['generic_name'] != med['brand_name'])
                        Text(med['generic_name'],
                            style: TextStyle(color: Colors.teal.shade700, fontSize: 13)),
                    ],
                  ),
                ),
                if (confidence == 'low')
                  const Icon(Icons.warning_amber, color: Colors.amber, size: 20)
                else
                  const Icon(Icons.check_circle, color: Colors.green, size: 20),
                IconButton(
                  icon: const Icon(Icons.edit, size: 18),
                  onPressed: () => _showEditDialog(index),
                  tooltip: 'Edit',
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline, size: 18, color: Colors.red),
                  onPressed: () => setState(() => _medications.removeAt(index)),
                  tooltip: 'Remove',
                ),
              ],
            ),
            const SizedBox(height: 6),
            // Row of field chips
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                if ((med['dosage'] ?? '').isNotEmpty)
                  Chip(
                    label: Text(med['dosage'], style: const TextStyle(fontSize: 11)),
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                  ),
                if ((med['frequency_english'] ?? '').isNotEmpty)
                  Chip(
                    label: Text(med['frequency_english'],
                        style: const TextStyle(fontSize: 11)),
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                    backgroundColor: Colors.blue.shade50,
                  ),
                ...timings.map((t) => Chip(
                      label: Text(t, style: const TextStyle(fontSize: 11)),
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                      backgroundColor: Colors.green.shade50,
                    )),
              ],
            ),
            if ((med['instruction'] ?? '').isNotEmpty ||
                (med['duration'] ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  [med['instruction'], med['duration']]
                      .where((s) => (s ?? '').isNotEmpty)
                      .join(' · '),
                  style: const TextStyle(color: Colors.grey, fontSize: 12),
                ),
              ),
            // Review issues (shown only when confidence < 80)
            _buildIssueChips(med),
          ],
        ),
      ),
    );
  }

  Widget _buildBlisterCard(Map<String, dynamic> med, int index) {
    final confidence = med['confidence'] ?? 'high';
    final totalTablets = med['total_tablets'];

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(med['brand_name'] ?? '',
                          style: const TextStyle(
                              fontSize: 16, fontWeight: FontWeight.bold)),
                      if ((med['generic_name'] ?? '').isNotEmpty &&
                          med['generic_name'] != med['brand_name'])
                        Text(med['generic_name'],
                            style: TextStyle(color: Colors.teal.shade700, fontSize: 13)),
                    ],
                  ),
                ),
                if (confidence == 'low')
                  const Icon(Icons.warning_amber, color: Colors.amber, size: 20)
                else
                  const Icon(Icons.check_circle, color: Colors.green, size: 20),
                IconButton(
                  icon: const Icon(Icons.edit, size: 18),
                  onPressed: () => _showEditDialog(index),
                  tooltip: 'Edit',
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline, size: 18, color: Colors.red),
                  onPressed: () => setState(() => _medications.removeAt(index)),
                  tooltip: 'Remove',
                ),
              ],
            ),
            const SizedBox(height: 6),
            // Row of blister-specific chips
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                if ((med['dosage'] ?? '').isNotEmpty)
                  Chip(
                    label: Text(med['dosage'], style: const TextStyle(fontSize: 11)),
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                  ),
                if (totalTablets != null)
                  Chip(
                    label: Text('$totalTablets tablets',
                        style: const TextStyle(fontSize: 11)),
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                    backgroundColor: Colors.blue.shade50,
                  ),
                if ((med['expiry_date'] ?? '').isNotEmpty)
                  Chip(
                    label: Text('Exp: ${med['expiry_date']}',
                        style: const TextStyle(fontSize: 11)),
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                    backgroundColor: Colors.orange.shade50,
                  ),
              ],
            ),
            if ((med['batch_no'] ?? '').isNotEmpty || (med['manufacturer'] ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  [
                    if ((med['batch_no'] ?? '').isNotEmpty) 'Batch: ${med['batch_no']}',
                    if ((med['manufacturer'] ?? '').isNotEmpty) med['manufacturer'],
                  ].join(' · '),
                  style: const TextStyle(color: Colors.grey, fontSize: 12),
                ),
              ),
            // Review issues (shown only when confidence < 80)
            _buildIssueChips(med),
          ],
        ),
      ),
    );
  }

  /// Renders orange issue chips under a card when _review_issues is non-empty.
  Widget _buildIssueChips(Map<String, dynamic> med) {
    final issues = med['_review_issues'];
    if (issues == null || issues is! List || issues.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Divider(height: 10),
          Wrap(
            spacing: 6,
            runSpacing: 4,
            children: issues.map<Widget>((issue) {
              return Chip(
                avatar: const Icon(Icons.warning_amber,
                    size: 14, color: Colors.orange),
                label: Text(issue.toString(),
                    style: const TextStyle(
                        fontSize: 10, color: Colors.deepOrange)),
                padding: EdgeInsets.zero,
                visualDensity: VisualDensity.compact,
                backgroundColor: Colors.orange.shade50,
                side: BorderSide(color: Colors.orange.shade200),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final typeLabel = _isBlister ? '💊 Medicine Pack' : '📋 Prescription';
    final typeBadgeColor = _isBlister ? Colors.blue.shade700 : Colors.teal.shade700;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Review Medications'),
        actions: [
          if (_isSaving)
            const Padding(
              padding: EdgeInsets.all(16),
              child: SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
              ),
            )
          else
            IconButton(
              icon: const Icon(Icons.check_circle_outline),
              tooltip: widget.requiresReview ? 'Confirm After Review' : 'Confirm & Save',
              onPressed: _medications.isEmpty ? null : _confirmMedications,
            ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _showAddManualDialog,
        icon: const Icon(Icons.add),
        label: const Text('Add Manually'),
      ),
      body: Column(
        children: [
          // Detected type badge
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: typeBadgeColor.withAlpha(20),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: typeBadgeColor,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(typeLabel,
                      style: const TextStyle(color: Colors.white, fontSize: 12)),
                ),
                const SizedBox(width: 8),
                Text('${_medications.length} medication${_medications.length == 1 ? '' : 's'} found',
                    style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
              ],
            ),
          ),

          // Disclaimer / review banner (adaptive)
          widget.requiresReview
              ? Container(
                  width: double.infinity,
                  color: Colors.orange.shade100,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 10),
                  child: const Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.warning_amber_rounded,
                          color: Colors.orange, size: 18),
                      SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '⚠️ Review Required — Some fields were ambiguous. Edit any card before confirming.',
                          style: TextStyle(
                              fontSize: 12,
                              color: Colors.deepOrange,
                              fontWeight: FontWeight.w600),
                        ),
                      ),
                    ],
                  ),
                )
              : Container(
                  width: double.infinity,
                  color: Colors.amber.shade100,
                  padding: const EdgeInsets.symmetric(
                      horizontal: 16, vertical: 10),
                  child: const Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.warning_amber_rounded,
                          color: Colors.amber, size: 18),
                      SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '⚠️ Review all medications carefully before confirming. This is not medical advice.',
                          style: TextStyle(
                              fontSize: 12, color: Colors.black87),
                        ),
                      ),
                    ],
                  ),
                ),

          // Medication list
          Expanded(
            child: _medications.isEmpty
                ? const Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(Icons.medication_outlined, size: 64, color: Colors.grey),
                        SizedBox(height: 16),
                        Text('No medications to confirm.',
                            style: TextStyle(color: Colors.grey)),
                        SizedBox(height: 8),
                        Text('Tap + to add one manually.',
                            style: TextStyle(color: Colors.grey, fontSize: 12)),
                      ],
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.only(top: 8, bottom: 80),
                    itemCount: _medications.length,
                    itemBuilder: (_, i) {
                      final srcType =
                          _medications[i]['source_type'] as String? ?? widget.detectedType;
                      return srcType == 'blister_pack'
                          ? _buildBlisterCard(_medications[i], i)
                          : _buildPrescriptionCard(_medications[i], i);
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

class _MedicationEditDialog extends StatefulWidget {
  final Map<String, dynamic> medication;
  final bool isBlister;

  const _MedicationEditDialog({
    required this.medication,
    required this.isBlister,
  });

  @override
  State<_MedicationEditDialog> createState() => _MedicationEditDialogState();
}

class _MedicationEditDialogState extends State<_MedicationEditDialog> {
  late TextEditingController _brandCtrl;
  late TextEditingController _dosageCtrl;
  late TextEditingController _expiryCtrl;

  late int _timesPerDay;
  late int _daysPerWeek;
  late List<TimeOfDay> _timings;

  @override
  void initState() {
    super.initState();
    _brandCtrl = TextEditingController(text: widget.medication['brand_name'] ?? '');
    _dosageCtrl = TextEditingController(text: widget.medication['dosage'] ?? '');
    _expiryCtrl = TextEditingController(text: widget.medication['expiry_date'] ?? '');

    // Parse initial timing
    final List<dynamic> rawTimings = widget.medication['timing'] as List? ?? [];
    _timings = [];
    for (var t in rawTimings) {
      if (t is String && t.contains(':')) {
        final parts = t.split(':');
        final hour = int.tryParse(parts[0]) ?? 8;
        final minute = int.tryParse(parts[1]) ?? 0;
        _timings.add(TimeOfDay(hour: hour, minute: minute));
      }
    }

    // Default to at least 1 dose time
    if (_timings.isEmpty) {
      _timings.add(const TimeOfDay(hour: 9, minute: 0));
    }
    _timesPerDay = _timings.length;

    // Parse days per week from frequency_english if possible, default to 7
    _daysPerWeek = 7;
    final freqEng = widget.medication['frequency_english'] as String? ?? '';
    final daysMatch = RegExp(r'(\d+)\s*days\s*a\s*week', caseSensitive: false).firstMatch(freqEng);
    if (daysMatch != null) {
      _daysPerWeek = int.tryParse(daysMatch.group(1) ?? '7') ?? 7;
    }
  }

  @override
  void dispose() {
    _brandCtrl.dispose();
    _dosageCtrl.dispose();
    _expiryCtrl.dispose();
    super.dispose();
  }

  String _formatTimeOfDay(TimeOfDay tod) {
    final hour = tod.hourOfPeriod == 0 ? 12 : tod.hourOfPeriod;
    final minute = tod.minute.toString().padLeft(2, '0');
    final period = tod.period == DayPeriod.am ? 'AM' : 'PM';
    return '$hour:$minute $period';
  }

  String _to24hString(TimeOfDay tod) {
    final hour = tod.hour.toString().padLeft(2, '0');
    final minute = tod.minute.toString().padLeft(2, '0');
    return '$hour:$minute';
  }

  Future<void> _selectTime(int index) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: _timings[index],
    );
    if (picked != null) {
      setState(() {
        _timings[index] = picked;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Edit Medication Details'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _brandCtrl,
              decoration: const InputDecoration(
                labelText: 'Brand Name',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _dosageCtrl,
              decoration: const InputDecoration(
                labelText: 'Dosage (e.g. 500mg)',
                border: OutlineInputBorder(),
              ),
            ),
            if (widget.isBlister) ...[
              const SizedBox(height: 12),
              TextField(
                controller: _expiryCtrl,
                decoration: const InputDecoration(
                  labelText: 'Expiry Date',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
            const SizedBox(height: 16),
            const Divider(),
            const SizedBox(height: 8),
            Text(
              'Frequency: $_timesPerDay times/day, $_daysPerWeek days/week',
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
            ),
            const SizedBox(height: 8),
            const Text('Times per day (slider):', style: TextStyle(fontSize: 12, color: Colors.grey)),
            Slider(
              value: _timesPerDay.toDouble(),
              min: 1,
              max: 6,
              divisions: 5,
              label: '$_timesPerDay times',
              onChanged: (val) {
                setState(() {
                  _timesPerDay = val.round();
                  while (_timings.length < _timesPerDay) {
                    final hours = [9, 13, 17, 21, 8, 20];
                    final idx = _timings.length;
                    final hour = idx < hours.length ? hours[idx] : 9;
                    _timings.add(TimeOfDay(hour: hour, minute: 0));
                  }
                  while (_timings.length > _timesPerDay) {
                    _timings.removeLast();
                  }
                });
              },
            ),
            const Text('Days per week (slider):', style: TextStyle(fontSize: 12, color: Colors.grey)),
            Slider(
              value: _daysPerWeek.toDouble(),
              min: 1,
              max: 7,
              divisions: 6,
              label: '$_daysPerWeek days',
              onChanged: (val) {
                setState(() {
                  _daysPerWeek = val.round();
                });
              },
            ),
            const SizedBox(height: 12),
            const Text(
              'Dose Timings:',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            ...List.generate(_timesPerDay, (index) {
              return Padding(
                padding: const EdgeInsets.only(bottom: 8.0),
                child: Row(
                  children: [
                    Text('Dose ${index + 1}:  ', style: const TextStyle(fontSize: 14)),
                    Expanded(
                      child: OutlinedButton.icon(
                        icon: const Icon(Icons.access_time, size: 18),
                        label: Text(_formatTimeOfDay(_timings[index])),
                        onPressed: () => _selectTime(index),
                      ),
                    ),
                  ],
                ),
              );
            }),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        ElevatedButton(
          onPressed: () {
            if (_brandCtrl.text.trim().isEmpty) return;

            final freqEnglish = '$_timesPerDay times a day, $_daysPerWeek days a week';
            final list24h = _timings.map((t) => _to24hString(t)).toList();

            final result = {
              'brand_name': _brandCtrl.text.trim(),
              'dosage': _dosageCtrl.text.trim(),
              'frequency_english': freqEnglish,
              'timing': list24h,
            };
            if (widget.isBlister) {
              result['expiry_date'] = _expiryCtrl.text.trim();
            }
            Navigator.pop(context, result);
          },
          child: const Text('Save'),
        ),
      ],
    );
  }
}
