import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../config/api_config.dart';
import 'scan_result_screen.dart';

class CameraScreen extends StatefulWidget {
  final String profileId;

  /// 'auto' | 'prescription' | 'blister_pack'
  final String imageType;

  const CameraScreen({
    required this.profileId,
    this.imageType = 'auto',
    super.key,
  });

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  final ImagePicker _picker = ImagePicker();
  bool _isProcessing = false;
  late String _imageType;

  @override
  void initState() {
    super.initState();
    // If auto, default to prescription; user can toggle
    _imageType = widget.imageType == 'blister_pack' ? 'blister_pack' : 'prescription';
  }

  bool get _isBlisterMode => _imageType == 'blister_pack';

  Future<String?> _getAuthToken() async {
    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user != null) {
        return await user.getIdToken();
      }
    } catch (_) {}
    // DEV fallback — backend DEV_MODE=true will accept this
    return 'dev-token';
  }

  Future<void> _captureAndScan(ImageSource source) async {
    final XFile? photo = await _picker.pickImage(
      source: source,
      imageQuality: 75, // Reduced from 90 — halves upload size; sufficient for OCR
      maxWidth: 1920,   // Downsample large photos; Gemini Vision doesn't need >1920px
      preferredCameraDevice: CameraDevice.rear,
    );
    if (photo == null) return;

    setState(() => _isProcessing = true);

    try {
      final file = File(photo.path);
      final token = await _getAuthToken();

      final formData = FormData.fromMap({
        'image': await MultipartFile.fromFile(
          file.path,
          filename: _isBlisterMode ? 'blister_pack.jpg' : 'prescription.jpg',
        ),
        'profile_id': widget.profileId,
        'image_type': _imageType,
      });

      final response = await Dio().post(
        '${ApiConfig.baseUrl}/api/v1/medications/scan',
        data: formData,
        options: Options(
          headers: {'Authorization': 'Bearer $token'},
          sendTimeout: const Duration(seconds: 60),
          receiveTimeout: const Duration(seconds: 60),
        ),
      );

      if (!mounted) return;

      final medications =
          List<Map<String, dynamic>>.from(response.data['medications']);
      final detectedType =
          response.data['detected_type'] as String? ?? _imageType;
      final requiresReview =
          response.data['requires_review'] as bool? ?? false;

      // Collect all issues from every medication for the review prompt
      final allIssues = <String>[];
      for (final med in medications) {
        final issues = med['_review_issues'];
        if (issues is List) {
          for (final issue in issues) {
            final s = issue.toString();
            if (s.isNotEmpty && !allIssues.contains(s)) allIssues.add(s);
          }
        }
      }

      if (requiresReview && allIssues.isNotEmpty) {
        // Show review prompt before navigating
        final proceed = await _showReviewPrompt(allIssues);
        if (!mounted || !proceed) return;
      }

      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => ScanResultScreen(
            medications: medications,
            profileId: widget.profileId,
            detectedType: detectedType,
            requiresReview: requiresReview,
          ),
        ),
      );
    } on DioException catch (e) {
      if (!mounted) return;
      final detail = e.response?.data?['detail']?.toString() ?? '';
      final message = (detail.contains('RESOURCE_EXHAUSTED') || detail.contains('429'))
          ? 'The AI is currently busy (rate limit). Please wait 30 seconds and try again.'
          : detail.isNotEmpty
              ? detail
              : 'Could not reach server. Check your connection.';
      _showError(message);
    } catch (e) {
      if (!mounted) return;
      _showError('Error scanning image: ${e.toString()}');
    } finally {
      if (mounted) setState(() => _isProcessing = false);
    }
  }

  /// Shows a bottom sheet warning about ambiguous scan results.
  /// Returns true if user wants to proceed to review, false to discard.
  Future<bool> _showReviewPrompt(List<String> issues) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isDismissible: false,
      enableDrag: false,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.warning_amber_rounded,
                    color: Colors.orange.shade700, size: 26),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    'Some details need your attention',
                    style: TextStyle(
                        fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'The scan result may be incomplete. Please review before saving.',
              style: TextStyle(fontSize: 13, color: Colors.black54),
            ),
            const SizedBox(height: 14),
            ...issues.map(
              (issue) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('• ',
                        style: TextStyle(
                            color: Colors.orange, fontWeight: FontWeight.bold)),
                    Expanded(
                        child: Text(issue,
                            style: const TextStyle(fontSize: 13))),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(ctx, false),
                    style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.red.shade700),
                    child: const Text('Discard'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  flex: 2,
                  child: ElevatedButton(
                    onPressed: () => Navigator.pop(ctx, true),
                    style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.orange.shade700,
                        foregroundColor: Colors.white),
                    child: const Text('Review & Edit'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
    return result ?? false;
  }

  void _showError(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        backgroundColor: Colors.red.shade700,
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final title = _isBlisterMode ? 'Scan Medicine Pack' : 'Scan Prescription';
    final tip = _isBlisterMode
        ? '💡 Focus on the foil side of the pack for best text recognition'
        : '💡 Keep prescription flat and well-lit for best results';

    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(title),
        elevation: 0,
      ),
      body: _isProcessing
          ? Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const CircularProgressIndicator(color: Colors.white),
                  const SizedBox(height: 20),
                  Text(
                    _isBlisterMode
                        ? 'Reading medicine pack...'
                        : 'Reading prescription...',
                    style: const TextStyle(color: Colors.white, fontSize: 16),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'This may take a few seconds',
                    style: TextStyle(color: Colors.white54, fontSize: 13),
                  ),
                ],
              ),
            )
          : Column(
              children: [
                // Mode toggle — only show when imageType == 'auto'
                if (widget.imageType == 'auto')
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                    child: SegmentedButton<String>(
                      segments: const [
                        ButtonSegment(
                          value: 'prescription',
                          label: Text('Prescription'),
                          icon: Icon(Icons.description),
                        ),
                        ButtonSegment(
                          value: 'blister_pack',
                          label: Text('Medicine Pack'),
                          icon: Icon(Icons.medication),
                        ),
                      ],
                      selected: {_imageType},
                      onSelectionChanged: (s) =>
                          setState(() => _imageType = s.first),
                      style: ButtonStyle(
                        foregroundColor:
                            WidgetStateProperty.all(Colors.white),
                      ),
                    ),
                  ),

                Expanded(
                  child: Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          _isBlisterMode
                              ? Icons.medication_rounded
                              : Icons.camera_alt,
                          size: 80,
                          color: Colors.white38,
                        ),
                        const SizedBox(height: 40),

                        // Primary: Camera
                        ElevatedButton.icon(
                          onPressed: () =>
                              _captureAndScan(ImageSource.camera),
                          icon: const Icon(Icons.camera_alt),
                          label: Text(
                            _isBlisterMode
                                ? 'Photograph Medicine Pack'
                                : 'Take Photo of Prescription',
                          ),
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.blue,
                            foregroundColor: Colors.white,
                            padding: const EdgeInsets.symmetric(
                                horizontal: 28, vertical: 14),
                            textStyle: const TextStyle(fontSize: 16),
                          ),
                        ),
                        const SizedBox(height: 16),

                        // Secondary: Gallery
                        TextButton.icon(
                          onPressed: () =>
                              _captureAndScan(ImageSource.gallery),
                          icon: const Icon(Icons.photo_library,
                              color: Colors.white54),
                          label: const Text(
                            'Choose from Gallery',
                            style: TextStyle(color: Colors.white54),
                          ),
                        ),
                        const SizedBox(height: 40),

                        // Tip
                        Padding(
                          padding:
                              const EdgeInsets.symmetric(horizontal: 32),
                          child: Text(
                            tip,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              color: Colors.white38,
                              fontSize: 12,
                            ),
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
