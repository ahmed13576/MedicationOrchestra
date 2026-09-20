import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:dio/dio.dart';
import 'firebase_options.dart';
import 'screens/camera_screen.dart';
import 'screens/interactions_screen.dart';
import 'screens/medications_screen.dart';
import 'screens/schedule_screen.dart';
import 'screens/family_screen.dart';
import 'services/fcm_service.dart';
import 'config/api_config.dart';
import 'screens/login_screen.dart';
import 'screens/about_screen.dart';
import 'screens/settings_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  );
  runApp(const MedicationOrchestraApp());
}

class MedicationOrchestraApp extends StatelessWidget {
  const MedicationOrchestraApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Medication Orchestra',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1A73E8),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
      ),
      home: StreamBuilder<User?>(
        stream: FirebaseAuth.instance.authStateChanges(),
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Scaffold(
              body: Center(child: CircularProgressIndicator()),
            );
          }
          if (snapshot.hasData) {
            return const HomeScreen();
          }
          return const LoginScreen();
        },
      ),
    );
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final Dio _dio = Dio();

  // Active profile — persisted in shared_preferences across app restarts.
  // Defaults to 'default' so the existing confirm flow keeps working.
  String _activeProfileId = 'default';
  String _activeProfileName = 'Default';

  // All household profiles fetched from the backend
  List<Map<String, dynamic>> _profiles = [];

  @override
  void initState() {
    super.initState();
    _loadActiveProfile();
    _loadProfiles().then((_) => _ensureDefaultProfile());
    // Initialize FCM — fire and forget, never blocks the UI
    FcmService.initialize(_dio, ApiConfig.baseUrl);
  }

  /// Guarantee at least one real profile exists.
  ///
  /// Saving a medicine needs a profile id the backend can store it under. On a
  /// fresh install there was none, and the app fell back to the literal string
  /// 'default' — so the medicines existed but belonged to a profile that never
  /// appeared in any list, and the household check could not scope them.
  Future<void> _ensureDefaultProfile() async {
    if (_profiles.isNotEmpty) {
      final known = _profiles.any((p) => p['profile_id'] == _activeProfileId);
      if (!known) {
        final first = _profiles.first;
        await _setActiveProfileQuietly(
          first['profile_id'] as String,
          (first['name'] as String?) ?? 'Me',
        );
      }
      return;
    }
    try {
      final token = await _getAuthToken();
      final response = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/profiles',
        data: {'name': 'Me'},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      final profile = {
        'profile_id': response.data['profile_id'],
        'name': response.data['name'],
      };
      if (mounted) setState(() => _profiles.add(profile));
      await _setActiveProfileQuietly(
        profile['profile_id'] as String,
        profile['name'] as String,
      );
    } catch (_) {
      // Offline first launch: the app still works read-only, and the user can
      // add a profile from the drawer once they are online.
    }
  }

  Future<void> _setActiveProfileQuietly(String id, String name) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('active_profile_id', id);
    await prefs.setString('active_profile_name', name);
    if (mounted) {
      setState(() {
        _activeProfileId = id;
        _activeProfileName = name;
      });
    }
  }

  // ── Auth ──────────────────────────────────────────────────────────────────

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  // ── Profile persistence ────────────────────────────────────────────────────

  Future<void> _loadActiveProfile() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _activeProfileId = prefs.getString('active_profile_id') ?? 'default';
      _activeProfileName = prefs.getString('active_profile_name') ?? 'Default';
    });
  }

  /// The profile id as the backend understands it.
  ///
  /// The app stores 'default' when no profile has been chosen; the API expects
  /// 'all' for "every patient, checked separately". Sending 'default' used to
  /// produce a 404 on every profile-scoped endpoint.
  String get _profileScope =>
      (_activeProfileId.isEmpty || _activeProfileId == 'default')
          ? 'all'
          : _activeProfileId;

  Future<void> _setActiveProfile(String id, String name) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('active_profile_id', id);
    await prefs.setString('active_profile_name', name);
    setState(() {
      _activeProfileId = id;
      _activeProfileName = name;
    });
    if (mounted) Navigator.pop(context); // close the drawer
  }

  // ── Profiles API ──────────────────────────────────────────────────────────

  Future<void> _loadProfiles() async {
    try {
      final token = await _getAuthToken();
      final response = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/profiles',
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          receiveTimeout: const Duration(seconds: 10),
        ),
      );
      final data = response.data as Map<String, dynamic>;
      final list = (data['profiles'] as List<dynamic>? ?? [])
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      if (mounted) setState(() => _profiles = list);
    } catch (_) {
      // Non-critical — profiles drawer will just show empty on first launch
    }
  }

  Future<void> _createProfile() async {
    final nameCtrl = TextEditingController();
    final submitted = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Add Household Member'),
        content: TextField(
          key: const Key('new_profile_name_field'),
          controller: nameCtrl,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: 'e.g. Mum, Dad, Child',
            border: OutlineInputBorder(),
          ),
          textCapitalization: TextCapitalization.words,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          ElevatedButton(
            key: const Key('add_profile_button'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Add'),
          ),
        ],
      ),
    );

    if (submitted != true || nameCtrl.text.trim().isEmpty || !mounted) return;
    final name = nameCtrl.text.trim();

    try {
      final token = await _getAuthToken();
      final response = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/profiles',
        data: {'name': name},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      final newProfile = {
        'profile_id': response.data['profile_id'],
        'name': response.data['name'],
      };
      setState(() => _profiles.add(newProfile));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to create profile: $e')),
        );
      }
    }
  }

  Future<void> _deleteProfile(String profileId, String profileName) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Profile'),
        content: Text(
            'Delete "$profileName" and all their medications? This cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            key: Key('confirm_delete_profile_$profileId'),
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    try {
      final token = await _getAuthToken();
      await _dio.delete(
        '${ApiConfig.baseUrl}/api/v1/profiles/$profileId',
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      setState(() => _profiles.removeWhere((p) => p['profile_id'] == profileId));

      // If the deleted profile was active, move to another real profile rather
      // than leaving the app pointed at a profile that no longer exists.
      if (_activeProfileId == profileId) {
        final remaining = _profiles
            .where((p) => p['profile_id'] != profileId)
            .toList();
        if (remaining.isNotEmpty) {
          await _setActiveProfileQuietly(
            remaining.first['profile_id'] as String,
            (remaining.first['name'] as String?) ?? 'Me',
          );
          await _ensureDefaultProfile();
        } else {
          await _ensureDefaultProfile();
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to delete profile: $e')),
        );
      }
    }
  }

  // ── Drawer ────────────────────────────────────────────────────────────────

  Widget _buildProfileAvatar(String name, String? color) {
    final bg = color != null
        ? Color(int.parse(color.replaceFirst('#', '0xFF')))
        : const Color(0xFF1A73E8);
    return CircleAvatar(
      backgroundColor: bg,
      radius: 18,
      child: Text(
        name.isNotEmpty ? name[0].toUpperCase() : '?',
        style: const TextStyle(
            color: Colors.white, fontSize: 14, fontWeight: FontWeight.bold),
      ),
    );
  }

  Widget _buildDrawer() {
    return Drawer(
      child: Column(
        children: [
          UserAccountsDrawerHeader(
            decoration: const BoxDecoration(color: Color(0xFF1A73E8)),
            accountName: Text(
              FirebaseAuth.instance.currentUser?.isAnonymous == true
                  ? 'Guest User'
                  : 'Medication Orchestra User',
              style: const TextStyle(fontWeight: FontWeight.bold),
            ),
            accountEmail: Text(FirebaseAuth.instance.currentUser?.email ?? 'Anonymous Session'),
            currentAccountPicture: const CircleAvatar(
              backgroundColor: Colors.white,
              child: Icon(Icons.medical_services, color: Color(0xFF1A73E8), size: 36),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                'Household Profiles',
                style: TextStyle(color: Colors.grey[600], fontSize: 13, fontWeight: FontWeight.bold),
              ),
            ),
          ),
          Expanded(
            child: ListView(
              padding: EdgeInsets.zero,
              children: [
                // Default profile tile
                _buildProfileTile(
                  profileId: 'all',
                  name: 'Default',
                  color: null,
                  canDelete: false,
                ),
                // User-created profiles (exclude 'default' — rendered above as hardcoded tile)
                ..._profiles
                    .where((p) => p['profile_id']?.toString() != 'default')
                    .map((p) {
                  final pid = p['profile_id']?.toString() ?? '';
                  final pname = p['name']?.toString() ?? pid;
                  final pcolor = p['avatar_color']?.toString();
                  return _buildProfileTile(
                    profileId: pid,
                    name: pname,
                    color: pcolor,
                    canDelete: true,
                  );
                }),
                const Divider(),
                // Family SOS Contacts
                ListTile(
                  key: const Key('family_sos_contacts_tile'),
                  leading: const Icon(Icons.family_restroom, color: Color(0xFF1A73E8)),
                  title: const Text('Family SOS Contacts'),
                  onTap: () {
                    Navigator.pop(context); // close drawer
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const FamilyScreen(),
                      ),
                    );
                  },
                ),
                const Divider(),
                // Add new profile
                ListTile(
                  key: const Key('add_new_profile_tile'),
                  leading: const CircleAvatar(
                    backgroundColor: Color(0xFFE8F0FE),
                    radius: 18,
                    child: Icon(Icons.add, color: Color(0xFF1A73E8), size: 20),
                  ),
                  title: const Text('Add Household Member'),
                  onTap: () async {
                    Navigator.pop(context); // close drawer first
                    await _createProfile();
                  },
                ),
                const Divider(),
                // Settings
                ListTile(
                  key: const Key('settings_tile'),
                  leading: const Icon(Icons.settings_outlined, color: Color(0xFF1A73E8)),
                  title: const Text('Settings'),
                  onTap: () {
                    Navigator.pop(context); // close drawer
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const SettingsScreen(),
                      ),
                    );
                  },
                ),
                const Divider(),
                // About
                ListTile(
                  key: const Key('about_tile'),
                  leading: const Icon(Icons.info_outline, color: Color(0xFF1A73E8)),
                  title: const Text('About App'),
                  onTap: () {
                    Navigator.pop(context); // close drawer
                    Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const AboutScreen(),
                      ),
                    );
                  },
                ),
                const Divider(),
                // Sign Out
                ListTile(
                  key: const Key('sign_out_tile'),
                  leading: const Icon(Icons.logout, color: Colors.red),
                  title: const Text('Sign Out', style: TextStyle(color: Colors.red)),
                  onTap: () async {
                    Navigator.pop(context); // close drawer
                    final prefs = await SharedPreferences.getInstance();
                    await prefs.clear(); // Wipe active profile cache
                    await FirebaseAuth.instance.signOut();
                  },
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildProfileTile({
    required String profileId,
    required String name,
    required String? color,
    required bool canDelete,
  }) {
    final isActive = profileId == _activeProfileId;
    return ListTile(
      key: Key('profile_tile_$profileId'),
      leading: _buildProfileAvatar(name, color),
      title: Text(name,
          style: TextStyle(
              fontWeight: isActive ? FontWeight.bold : FontWeight.normal)),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (isActive)
            const Icon(Icons.check_circle, color: Color(0xFF1A73E8), size: 20),
          if (canDelete && !isActive)
            IconButton(
              key: Key('delete_profile_btn_$profileId'),
              icon: const Icon(Icons.delete_outline, color: Colors.red, size: 20),
              onPressed: () => _deleteProfile(profileId, name),
            ),
        ],
      ),
      selected: isActive,
      selectedTileColor: const Color(0xFFE8F0FE),
      onTap: () => _setActiveProfile(profileId, name),
    );
  }

  // ── Home UI ───────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Medication Orchestra'),
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        actions: [
          // Active profile chip in AppBar
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Chip(
              key: const Key('active_profile_chip'),
              avatar: _buildProfileAvatar(_activeProfileName, null),
              label: Text(
                _activeProfileName,
                style: const TextStyle(fontSize: 12),
              ),
              padding: EdgeInsets.zero,
            ),
          ),
        ],
      ),
      drawer: SafeArea(child: _buildDrawer()),
      floatingActionButton: FloatingActionButton.extended(
        key: const Key('sos_fab'),
        onPressed: _showSosDialog,
        backgroundColor: Colors.red,
        foregroundColor: Colors.white,
        icon: const Icon(Icons.warning_rounded),
        label: const Text('SOS'),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.medical_services,
                    size: 72, color: Color(0xFF1A73E8)),
                const SizedBox(height: 20),
                const Text(
                  'Medication Orchestra',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 26, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 6),
                const Text(
                  'Household Medication Safety Agent',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 15, color: Colors.grey),
                ),
                const SizedBox(height: 48),

                // Scan prescription
                ElevatedButton.icon(
                  key: const Key('scan_prescription_button'),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => CameraScreen(
                        profileId: _activeProfileId,
                        imageType: 'auto',
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.document_scanner),
                  label: const Text('Scan Prescription or Medicine Pack'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF1A73E8),
                    foregroundColor: Colors.white,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                    textStyle: const TextStyle(fontSize: 16),
                  ),
                ),
                const SizedBox(height: 12),

                // My Medications ledger
                OutlinedButton.icon(
                  key: const Key('my_medications_button'),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => MedicationsScreen(
                        profileId: _activeProfileId,
                        profileName: _activeProfileName,
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.list_alt_rounded),
                  label: Text("$_activeProfileName's Medications"),
                  style: OutlinedButton.styleFrom(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                    textStyle: const TextStyle(fontSize: 15),
                  ),
                ),
                const SizedBox(height: 12),

                // Dose schedule
                OutlinedButton.icon(
                  key: const Key('view_schedule_button'),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ScheduleScreen(
                        profileId: _profileScope,
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.schedule_rounded),
                  label: const Text('View Dose Schedule'),
                  style: OutlinedButton.styleFrom(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                    textStyle: const TextStyle(fontSize: 15),
                  ),
                ),
                const SizedBox(height: 16),

                // Interaction checker
                ElevatedButton.icon(
                  key: const Key('check_interactions_button'),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => InteractionsScreen(
                        profileId: _profileScope,
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.warning_amber_rounded),
                  label: const Text('Check Interactions'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.deepOrange,
                    foregroundColor: Colors.white,
                    minimumSize: const Size.fromHeight(50),
                    padding:
                        const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                    textStyle: const TextStyle(fontSize: 15),
                  ),
                ),
                const SizedBox(height: 32),

                const Text(
                  'DISCLAIMER: This application provides general drug interaction '
                  'information for educational purposes only. It is not a substitute '
                  'for professional medical advice. Always consult your doctor or '
                  'pharmacist before changing medication schedules.',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 11, color: Colors.grey),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// Show the SOS confirmation and send the emergency alert.
  ///
  /// Two things changed here, both of which used to make SOS do nothing at all:
  ///   1. the alert is identified by `alert_id`, the field the API actually
  ///      accepts (the old payload sent `interaction_id`, which the backend's
  ///      model does not carry, so every SOS was a 422/404);
  ///   2. the id comes from the check response the *server* just produced and
  ///      persisted, so the backend can validate it before notifying anyone.
  /// The delivery outcome is reported to the user verbatim: "sent to everyone"
  /// is only shown when everyone was actually reached.
  Future<void> _showSosDialog() async {
    try {
      final token = await _getAuthToken();
      final resp = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/interactions',
        queryParameters: {'profile_id': _profileScope},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      final interactions = (resp.data['interactions'] as List<dynamic>?) ?? [];
      if (interactions.isEmpty) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'There is nothing to alert anyone about: no interactions are '
              'currently flagged for this profile.',
            ),
          ),
        );
        return;
      }

      final top = Map<String, dynamic>.from(interactions.first as Map);
      final title = (top['title'] as String?) ?? 'a medication risk';

      if (!mounted) return;
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('\u{1F6A8} Send SOS alert?'),
          content: Text(
            'This will immediately notify your registered family contacts about:\n\n'
            '$title\n\n'
            'It is meant for a real emergency. Your contacts will be told which '
            'medicines are involved and what to watch for.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            ElevatedButton(
              key: const Key('sos_confirm_button'),
              onPressed: () => Navigator.pop(ctx, true),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.red,
                foregroundColor: Colors.white,
              ),
              child: const Text('Send alert'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted) return;

      final sos = await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/sos/alert',
        data: {
          'alert_id': top['id'] ?? '',
          'patient_name': _activeProfileName,
        },
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );

      final sent = sos.data['sent_to'] ?? 0;
      final failed = sos.data['failed'] ?? 0;
      final total = sos.data['total_family_members'] ?? sent;
      final skipped = (sos.data['skipped'] as List<dynamic>?) ?? const [];

      if (!mounted) return;
      final reachedEveryone = sent == total && failed == 0 && skipped.isEmpty;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            reachedEveryone
                ? '\u{1F6A8} Alert delivered to all $sent contact(s).'
                : 'Alert delivered to $sent of $total contact(s).'
                    '${skipped.isNotEmpty ? " ${skipped.length} could not be reached." : ""}'
                    '${failed > 0 ? " $failed failed to send." : ""}',
          ),
          backgroundColor: reachedEveryone ? Colors.red : Colors.orange.shade800,
          duration: const Duration(seconds: 6),
        ),
      );
    } on DioException catch (e) {
      if (!mounted) return;
      final detail = e.response?.data?['detail']?.toString();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            detail?.isNotEmpty == true
                ? detail!
                : 'Could not send the alert. Check your connection and try again.',
          ),
          backgroundColor: Colors.red.shade900,
          duration: const Duration(seconds: 8),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('SOS failed: $e')),
      );
    }
  }
}
