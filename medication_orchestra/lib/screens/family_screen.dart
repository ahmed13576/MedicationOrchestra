import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../config/api_config.dart';

/// Manages family member SOS contacts.
/// Family members receive FCM push notifications when SOS is triggered.
class FamilyScreen extends StatefulWidget {
  const FamilyScreen({super.key});

  @override
  State<FamilyScreen> createState() => _FamilyScreenState();
}

class _FamilyScreenState extends State<FamilyScreen> {
  final Dio _dio = Dio();

  bool _isLoading = true;
  String? _error;
  List<Map<String, dynamic>> _members = [];

  @override
  void initState() {
    super.initState();
    _loadMembers();
  }

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  Future<void> _loadMembers() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final token = await _getAuthToken();
      final resp = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/family',
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          receiveTimeout: const Duration(seconds: 10),
        ),
      );
      final data = resp.data as Map<String, dynamic>;
      final list = (data['members'] as List<dynamic>? ?? [])
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      setState(() {
        _members = list;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _error = 'Failed to load family contacts: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _deleteMember(String memberId, String name) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Remove Contact'),
        content: Text('Remove "$name" from SOS contacts?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            key: Key('confirm_delete_member_$memberId'),
            onPressed: () => Navigator.pop(ctx, true),
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
        '${ApiConfig.baseUrl}/api/v1/family/$memberId',
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      setState(() {
        _members.removeWhere((m) => m['member_id'] == memberId);
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('"$name" removed from SOS contacts.')),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to remove contact: $e')),
      );
    }
  }

  Future<void> _showAddMemberSheet() async {
    final nameCtrl = TextEditingController();
    final tokenCtrl = TextEditingController();
    final formKey = GlobalKey<FormState>();

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 24,
          right: 24,
          top: 24,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
        ),
        child: Form(
          key: formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Add Family SOS Contact',
                style: TextStyle(
                    fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 6),
              const Text(
                'Ask your family member to open the Medication Orchestra app → '
                'Settings → "Copy My Device Token", then paste it here.',
                style: TextStyle(fontSize: 12, color: Colors.grey),
              ),
              const SizedBox(height: 16),
              TextFormField(
                key: const Key('family_member_name_field'),
                controller: nameCtrl,
                decoration: const InputDecoration(
                  labelText: 'Name',
                  hintText: 'e.g. Mum, Brother, Wife',
                  border: OutlineInputBorder(),
                ),
                textCapitalization: TextCapitalization.words,
                validator: (v) =>
                    (v == null || v.trim().isEmpty) ? 'Name is required' : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                key: const Key('family_member_token_field'),
                controller: tokenCtrl,
                decoration: const InputDecoration(
                  labelText: 'Device Token (FCM)',
                  hintText: 'Paste the token from the family member\'s app',
                  border: OutlineInputBorder(),
                ),
                maxLines: 2,
                validator: (v) =>
                    (v == null || v.trim().isEmpty)
                        ? 'Device token is required'
                        : null,
              ),
              const SizedBox(height: 16),
              ElevatedButton(
                key: const Key('add_family_member_button'),
                onPressed: () async {
                  if (!formKey.currentState!.validate()) return;
                  Navigator.pop(ctx); // close sheet
                  await _addMember(
                    nameCtrl.text.trim(),
                    tokenCtrl.text.trim(),
                  );
                },
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF1A73E8),
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
                child: const Text('Add Contact'),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _addMember(String name, String fcmToken) async {
    try {
      final token = await _getAuthToken();
      final resp = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/family',
        data: {'name': name, 'fcm_token': fcmToken},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      final newMember = {
        'member_id': resp.data['member_id'],
        'name': name,
        'fcm_token': fcmToken,
      };
      setState(() => _members.add(newMember));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('"$name" added to SOS contacts!')),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed to add contact: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Family SOS Contacts'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
      ),
      floatingActionButton: FloatingActionButton(
        key: const Key('add_family_member_fab'),
        onPressed: _showAddMemberSheet,
        backgroundColor: const Color(0xFF1A73E8),
        foregroundColor: Colors.white,
        child: const Icon(Icons.person_add),
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, size: 48, color: Colors.red),
              const SizedBox(height: 12),
              Text(_error!, textAlign: TextAlign.center),
              const SizedBox(height: 20),
              ElevatedButton.icon(
                onPressed: _loadMembers,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
              ),
            ],
          ),
        ),
      );
    }

    if (_members.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.family_restroom, size: 64, color: Colors.grey),
              SizedBox(height: 16),
              Text(
                'No family SOS contacts yet.',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                textAlign: TextAlign.center,
              ),
              SizedBox(height: 8),
              Text(
                'Tap + to add a family member.\nThey will receive emergency alerts '
                'when you tap the SOS button.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey, fontSize: 14),
              ),
            ],
          ),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Text(
            '${_members.length} contact(s) will receive SOS alerts',
            style: const TextStyle(fontSize: 13, color: Colors.grey),
          ),
        ),
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: _members.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (ctx, i) {
              final member = _members[i];
              final id = member['member_id']?.toString() ?? '';
              final name = member['name']?.toString() ?? 'Unknown';

              return ListTile(
                key: Key('family_member_tile_$id'),
                leading: CircleAvatar(
                  backgroundColor: const Color(0xFFE8F0FE),
                  child: Text(
                    name.isNotEmpty ? name[0].toUpperCase() : '?',
                    style: const TextStyle(
                      color: Color(0xFF1A73E8),
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                title: Text(name,
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: const Text('SOS contact',
                    style: TextStyle(fontSize: 12, color: Colors.grey)),
                trailing: IconButton(
                  key: Key('delete_member_btn_$id'),
                  icon: const Icon(Icons.delete_outline, color: Colors.red),
                  tooltip: 'Remove contact',
                  onPressed: () => _deleteMember(id, name),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}
