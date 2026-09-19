import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import '../config/api_config.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final Dio _dio = Dio();
  String? _fcmToken;
  int _sosRateLimitHours = 2; // Default to 2 hours
  bool _isLoadingToken = true;
  bool _isSaving = false;

  /// True when the server could not be asked for the current value, so the
  /// slider shows the default rather than the user's setting. The screen says
  /// so instead of presenting a default as if it were saved.
  bool _settingsLoadFailed = false;

  @override
  void initState() {
    super.initState();
    _loadFcmToken();
    _loadSettings();
  }

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) return await user.getIdToken();
    } catch (_) {}
    return 'dev-token';
  }

  Future<void> _loadFcmToken() async {
    try {
      final token = await FirebaseMessaging.instance.getToken();
      setState(() {
        _fcmToken = token;
        _isLoadingToken = false;
      });
    } catch (e) {
      setState(() {
        _fcmToken = 'Error fetching token: $e';
        _isLoadingToken = false;
      });
    }
  }

  Future<void> _loadSettings() async {
    try {
      final token = await _getAuthToken();
      final response = await _dio.get(
        '${ApiConfig.baseUrl}/api/v1/users/settings',
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      final data = response.data as Map<String, dynamic>;
      final loaded = (data['sos_rate_limit_hours'] as num?)?.toInt();
      if (!mounted) return;
      setState(() {
        if (loaded != null) {
          _sosRateLimitHours = loaded;
          _settingsLoadFailed = false;
        } else {
          _settingsLoadFailed = true;
        }
      });
    } catch (_) {
      // Keep the default visible, but say that it is a default.
      if (mounted) setState(() => _settingsLoadFailed = true);
    }
  }

  Future<void> _saveSettings(int value) async {
    final previous = _sosRateLimitHours;
    setState(() {
      _isSaving = true;
      _sosRateLimitHours = value;
    });

    try {
      final token = await _getAuthToken();
      await _dio.post(
        '${ApiConfig.baseUrl}/api/v1/users/settings',
        data: {'sos_rate_limit_hours': value},
        options: Options(headers: {'Authorization': 'Bearer $token'}),
      );
      if (mounted) {
        setState(() => _settingsLoadFailed = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Settings saved.')),
        );
      }
    } catch (e) {
      // Put the old value back: a slider that shows a setting the server did
      // not accept is a lie about how the next SOS will behave.
      if (mounted) {
        setState(() => _sosRateLimitHours = previous);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Could not save settings: '
                '${e is DioException && e.response?.statusCode == 401 ? 'please sign in again' : e}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) {
        setState(() {
          _isSaving = false;
        });
      }
    }
  }

  void _copyToClipboard() {
    if (_fcmToken != null && !_fcmToken!.startsWith('Error')) {
      Clipboard.setData(ClipboardData(text: _fcmToken!));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('✅ Device token copied to clipboard!')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // FCM token section
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.phone_android, color: Color(0xFF1A73E8)),
                      SizedBox(width: 8),
                      Text(
                        'My Device Token (FCM)',
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Share this token with family members so they can add you as an SOS contact.',
                    style: TextStyle(fontSize: 13, color: Colors.grey),
                  ),
                  const SizedBox(height: 12),
                  if (_isLoadingToken)
                    const Center(child: CircularProgressIndicator())
                  else ...[
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Colors.grey.shade100,
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: Colors.grey.shade300),
                      ),
                      child: SelectableText(
                        _fcmToken ?? 'No token found',
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                        maxLines: 4,
                      ),
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton.icon(
                        icon: const Icon(Icons.copy),
                        label: const Text('Copy My Device Token'),
                        onPressed: _fcmToken != null && !_fcmToken!.startsWith('Error') ? _copyToClipboard : null,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          // SOS rate limit section
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.alarm, color: Colors.red),
                      const SizedBox(width: 8),
                      const Text(
                        'SOS Alert Rate Limit',
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                      if (_isSaving) ...[
                        const SizedBox(width: 12),
                        const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Configure how frequently other users can send you emergency SOS alerts. This prevents spamming.',
                    style: TextStyle(fontSize: 13, color: Colors.grey),
                  ),
                  const SizedBox(height: 16),
                  DropdownButtonFormField<int>(
                    value: _sosRateLimitHours,
                    decoration: const InputDecoration(
                      labelText: 'Minimum Interval',
                      border: OutlineInputBorder(),
                    ),
                    items: const [
                      DropdownMenuItem(value: 1, child: Text('1 Hour')),
                      DropdownMenuItem(value: 2, child: Text('2 Hours (Recommended)')),
                      DropdownMenuItem(value: 4, child: Text('4 Hours')),
                      DropdownMenuItem(value: 8, child: Text('8 Hours')),
                      DropdownMenuItem(value: 12, child: Text('12 Hours')),
                      DropdownMenuItem(value: 24, child: Text('24 Hours')),
                    ],
                    onChanged: _isSaving ? null : (val) {
                      if (val != null) {
                        _saveSettings(val);
                      }
                    },
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _settingsLoadFailed
                        ? 'Showing the default (2 hours) because your saved setting '
                            'could not be loaded. Changing it will save it.'
                        : _isSaving
                            ? 'Saving…'
                            : 'Saved on your account.',
                    style: TextStyle(
                      fontSize: 12,
                      color: _settingsLoadFailed
                          ? Colors.orange.shade900
                          : Colors.grey.shade700,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
