import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../config/api_config.dart';

/// Displays the medication ledger for a specific household profile.
///
/// Shows all active medications with options to edit fields or soft-delete.
class MedicationsScreen extends StatefulWidget {
  final String profileId;
  final String profileName;

  const MedicationsScreen({
    super.key,
    required this.profileId,
    required this.profileName,
  });

  @override
  State<MedicationsScreen> createState() => _MedicationsScreenState();
}

class _MedicationsScreenState extends State<MedicationsScreen> {
  final Dio _dio = Dio();
  bool _isLoading = true;
  String? _error;
  List<Map<String, dynamic>> _medications = [];

  @override
  void initState() {
    super.initState();
    _loadMedications();
  }

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  Future<void> _loadMedications() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final token = await _getAuthToken();
      final response = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/profiles/${widget.profileId}/medications',
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          receiveTimeout: const Duration(seconds: 30),
        ),
      );
      final data = response.data as Map<String, dynamic>;
      final list = (data['medications'] as List<dynamic>? ?? [])
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      setState(() {
        _medications = list;
        _isLoading = false;
      });
    } on DioException catch (e) {
      setState(() {
        _error = e.response?.data?['detail']?.toString() ??
            'Failed to load medications. Please try again.';
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _error = 'Unexpected error: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _deleteMedication(String medId, int index) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Remove Medication'),
        content: Text(
          'Remove "${_medications[index]['brand_name'] ?? 'this medication'}" from ${widget.profileName}\'s list?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            key: const Key('confirm_delete_button'),
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    try {
      final token = await _getAuthToken();
      await _dio.delete(
        '${ApiConfig.baseUrl}/api/v1/profiles/${widget.profileId}/medications/$medId',
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      setState(() => _medications.removeAt(index));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Medication removed')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to remove: $e')),
        );
      }
    }
  }

  Future<void> _editMedication(Map<String, dynamic> med, int index) async {
    final brandCtrl =
        TextEditingController(text: med['brand_name']?.toString() ?? '');
    final genericCtrl =
        TextEditingController(text: med['generic_name']?.toString() ?? '');
    final dosageCtrl =
        TextEditingController(text: med['dosage']?.toString() ?? '');
    final freqCtrl = TextEditingController(
        text: med['frequency_english']?.toString() ?? '');

    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 20,
          right: 20,
          top: 24,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Edit Medication',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 16),
            TextField(
              key: const Key('edit_brand_name'),
              controller: brandCtrl,
              decoration: const InputDecoration(
                labelText: 'Brand Name',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('edit_generic_name'),
              controller: genericCtrl,
              decoration: const InputDecoration(
                labelText: 'Generic / Composition',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('edit_dosage'),
              controller: dosageCtrl,
              decoration: const InputDecoration(
                labelText: 'Dosage (e.g. 500mg)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('edit_frequency'),
              controller: freqCtrl,
              decoration: const InputDecoration(
                labelText: 'Frequency (e.g. Twice daily)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 20),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: const Text('Cancel'),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  key: const Key('save_edit_button'),
                  onPressed: () => Navigator.pop(ctx, true),
                  child: const Text('Save'),
                ),
              ],
            ),
          ],
        ),
      ),
    );

    if (saved != true || !mounted) return;

    final updates = <String, String>{};
    if (brandCtrl.text.trim() != (med['brand_name'] ?? '')) {
      updates['brand_name'] = brandCtrl.text.trim();
    }
    if (genericCtrl.text.trim() != (med['generic_name'] ?? '')) {
      updates['generic_name'] = genericCtrl.text.trim();
    }
    if (dosageCtrl.text.trim() != (med['dosage'] ?? '')) {
      updates['dosage'] = dosageCtrl.text.trim();
    }
    if (freqCtrl.text.trim() != (med['frequency_english'] ?? '')) {
      updates['frequency_english'] = freqCtrl.text.trim();
    }
    if (updates.isEmpty) return;

    try {
      final token = await _getAuthToken();
      await _dio.patch(
        '${ApiConfig.baseUrl}/api/v1/profiles/${widget.profileId}/medications/${med['id']}',
        data: updates,
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      setState(() {
        _medications[index] = {...med, ...updates};
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Medication updated')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to update: $e')),
        );
      }
    }
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.medication_outlined, size: 64, color: Colors.grey[400]),
          const SizedBox(height: 16),
          Text(
            'No medications yet for ${widget.profileName}',
            style: TextStyle(fontSize: 16, color: Colors.grey[600]),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 8),
          Text(
            'Scan a prescription or medicine pack to add medications.',
            style: TextStyle(fontSize: 13, color: Colors.grey[500]),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  Widget _buildMedCard(Map<String, dynamic> med, int index) {
    final brand = med['brand_name']?.toString() ?? 'Unknown';
    final generic = med['generic_name']?.toString() ?? '';
    final dosage = med['dosage']?.toString() ?? '';
    final freq = med['frequency_english']?.toString() ?? '';
    final medId = med['id']?.toString() ?? '';

    return Card(
      key: Key('med_card_$medId'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.medication_rounded,
                color: Color(0xFF1A73E8), size: 32),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    brand,
                    style: const TextStyle(
                        fontSize: 15, fontWeight: FontWeight.bold),
                  ),
                  if (generic.isNotEmpty && generic != brand) ...[
                    const SizedBox(height: 2),
                    Text(
                      generic,
                      style:
                          TextStyle(fontSize: 13, color: Colors.grey[700]),
                    ),
                  ],
                  if (dosage.isNotEmpty || freq.isNotEmpty)
                    const SizedBox(height: 4),
                  Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    children: [
                      if (dosage.isNotEmpty)
                        _Pill(label: dosage, color: Colors.blue[50]!),
                      if (freq.isNotEmpty)
                        _Pill(label: freq, color: Colors.green[50]!),
                    ],
                  ),
                ],
              ),
            ),
            Column(
              children: [
                IconButton(
                  key: Key('edit_med_$medId'),
                  icon: const Icon(Icons.edit_outlined, size: 20),
                  tooltip: 'Edit',
                  onPressed: () => _editMedication(med, index),
                ),
                IconButton(
                  key: Key('delete_med_$medId'),
                  icon: const Icon(Icons.delete_outline,
                      size: 20, color: Colors.red),
                  tooltip: 'Remove',
                  onPressed: () => _deleteMedication(medId, index),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.profileName}\'s Medications'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          IconButton(
            key: const Key('refresh_medications_button'),
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _loadMedications,
          ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Icon(Icons.error_outline,
                            color: Colors.red, size: 48),
                        const SizedBox(height: 12),
                        Text(_error!, textAlign: TextAlign.center),
                        const SizedBox(height: 16),
                        ElevatedButton(
                          onPressed: _loadMedications,
                          child: const Text('Retry'),
                        ),
                      ],
                    ),
                  ),
                )
              : _medications.isEmpty
                  ? _buildEmptyState()
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      itemCount: _medications.length,
                      itemBuilder: (ctx, i) =>
                          _buildMedCard(_medications[i], i),
                    ),
    );
  }
}

/// Small pill-shaped label chip used in the medication card.
class _Pill extends StatelessWidget {
  final String label;
  final Color color;
  const _Pill({required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(maxWidth: 200),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: const TextStyle(fontSize: 11),
        softWrap: true,
      ),
    );
  }
}
