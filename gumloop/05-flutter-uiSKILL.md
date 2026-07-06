day 4 /home/user/medication_orchestra/.agents/skills/05-flutter-ui/SKILL.md
---
name: flutter-ui
description: Builds all Flutter UI screens for Medication Orchestra: home dashboard, interaction alert cards, schedule timeline, and SOS emergency screen. Use this skill for all frontend UI work, widget implementation, navigation wiring, and Day 4 polish. References the app screen map from the technical spec.
---

# Flutter UI Skill

## App Color System
```dart
// lib/theme.dart
import 'package:flutter/material.dart';

class AppColors {
  static const primary = Color(0xFF1565C0);      // Medical blue
  static const background = Color(0xFFF5F7FA);
  static const surface = Colors.white;
  
  // Severity colors (match 1mg.com / DrugBank standard)
  static const contraindicated = Color(0xFFB71C1C); // Deep red
  static const severityMajor = Color(0xFFE64A19);   // Deep orange
  static const severityModerate = Color(0xFFF57F17); // Amber
  static const severityMinor = Color(0xFF1565C0);   // Blue
  
  static Color forSeverity(String severity) {
    switch (severity.toLowerCase()) {
      case 'contraindicated': return contraindicated;
      case 'major': return severityMajor;
      case 'moderate': return severityModerate;
      case 'minor': return severityMinor;
      default: return Colors.grey;
    }
  }
  
  static IconData iconForSeverity(String severity) {
    switch (severity.toLowerCase()) {
      case 'contraindicated': return Icons.block;
      case 'major': return Icons.warning_rounded;
      case 'moderate': return Icons.warning_amber_rounded;
      default: return Icons.info_outline;
    }
  }
}
```

## Screen 1: Home Dashboard (lib/screens/home_screen.dart)

```dart
import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import '../theme.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
        title: Row(
          children: [
            const Text('💊 ', style: TextStyle(fontSize: 20)),
            const Text('Medication Orchestra'),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.person_add),
            onPressed: () => Navigator.pushNamed(context, '/add-profile'),
            tooltip: 'Add Family Member',
          ),
        ],
      ),
      body: StreamBuilder<QuerySnapshot>(
        stream: _interactionsStream(),
        builder: (context, snapshot) {
          final interactions = snapshot.data?.docs ?? [];
          final criticalCount = interactions.where((d) {
            final sev = d['severity'] as String? ?? '';
            return sev == 'contraindicated' || sev == 'major';
          }).length;
          
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              // Critical alert banner (if any)
              if (criticalCount > 0)
                _buildCriticalBanner(context, criticalCount),
              
              const SizedBox(height: 16),
              
              // Quick action buttons
              Row(
                children: [
                  Expanded(
                    child: _ActionCard(
                      icon: Icons.camera_alt,
                      label: 'Scan\nPrescription',
                      color: AppColors.primary,
                      onTap: () => Navigator.pushNamed(context, '/camera'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _ActionCard(
                      icon: Icons.warning_rounded,
                      label: 'Check\nInteractions',
                      color: AppColors.severityMajor,
                      onTap: () => _triggerInteractionCheck(context),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _ActionCard(
                      icon: Icons.schedule,
                      label: "Today's\nSchedule",
                      color: Colors.teal,
                      onTap: () => Navigator.pushNamed(context, '/schedule'),
                    ),
                  ),
                ],
              ),
              
              const SizedBox(height: 24),
              
              // Interactions list
              if (interactions.isNotEmpty) ...[
                const Text('⚠️ Medication Interactions', 
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                ...interactions.map((doc) => InteractionCard(
                  interaction: doc.data() as Map<String, dynamic>,
                  interactionId: doc.id,
                )),
              ] else
                _buildNoInteractionsCard(),
              
              const SizedBox(height: 32),
              
              // SOS Button
              _buildSOSButton(context, interactions),
            ],
          );
        },
      ),
    );
  }
  
  Widget _buildCriticalBanner(BuildContext context, int count) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.severityMajor.withOpacity(0.1),
        border: Border.all(color: AppColors.severityMajor),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_rounded, color: AppColors.severityMajor),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '$count critical interaction(s) detected. Review below.',
              style: const TextStyle(color: AppColors.severityMajor, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
  
  Widget _buildSOSButton(BuildContext context, List<QueryDocumentSnapshot> interactions) {
    final hasCritical = interactions.any((d) {
      final sev = d['severity'] as String? ?? '';
      return sev == 'contraindicated' || sev == 'major';
    });
    
    if (!hasCritical) return const SizedBox.shrink();
    
    return SizedBox(
      width: double.infinity,
      height: 56,
      child: ElevatedButton.icon(
        onPressed: () => Navigator.pushNamed(context, '/sos',
          arguments: interactions.where((d) {
            final sev = d['severity'] as String? ?? '';
            return sev == 'contraindicated' || sev == 'major';
          }).first),
        icon: const Icon(Icons.emergency, size: 28),
        label: const Text('🚨 Send Emergency Alert to Family', 
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.severityMajor,
          foregroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
    );
  }
}
```

## Screen 2: Interaction Alert Card Widget (lib/widgets/interaction_card.dart)

```dart
import 'package:flutter/material.dart';
import '../theme.dart';

class InteractionCard extends StatefulWidget {
  final Map<String, dynamic> interaction;
  final String interactionId;
  
  const InteractionCard({
    required this.interaction, 
    required this.interactionId,
    super.key
  });

  @override
  State<InteractionCard> createState() => _InteractionCardState();
}

class _InteractionCardState extends State<InteractionCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final severity = widget.interaction['severity'] as String? ?? 'minor';
    final color = AppColors.forSeverity(severity);
    final icon = AppColors.iconForSeverity(severity);
    
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: color.withOpacity(0.4), width: 1),
      ),
      child: Column(
        children: [
          // Header (always visible)
          ListTile(
            leading: Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: color.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(icon, color: color, size: 24),
            ),
            title: Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: color,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    severity.toUpperCase(),
                    style: const TextStyle(color: Colors.white, fontSize: 10, 
                      fontWeight: FontWeight.bold),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    widget.interaction['title'] ?? '',
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '${widget.interaction['med_a_name']} + ${widget.interaction['med_b_name']}',
                style: TextStyle(color: Colors.grey[600], fontSize: 12),
              ),
            ),
            trailing: Icon(_expanded ? Icons.expand_less : Icons.expand_more),
            onTap: () => setState(() => _expanded = !_expanded),
          ),
          
          // Expanded details
          if (_expanded) ...[
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Plain language explanation
                  _Section('What happens:', 
                    widget.interaction['explanation'] ?? ''),
                  
                  const SizedBox(height: 12),
                  
                  // What to do
                  _Section('What to do:', 
                    widget.interaction['what_to_do'] ?? '', 
                    iconData: Icons.check_circle_outline, 
                    color: Colors.green[700]!),
                  
                  // Time gap (if applicable)
                  if ((widget.interaction['time_gap_hours'] ?? 0) > 0) ...[
                    const SizedBox(height: 12),
                    _Section(
                      '⏰ Safe timing:',
                      widget.interaction['time_gap_note'] ?? '',
                      color: Colors.teal,
                    ),
                  ],
                  
                  // Safe alternative
                  if (widget.interaction['safe_alternative'] != null) ...[
                    const SizedBox(height: 12),
                    _Section(
                      '💊 Safer option:',
                      widget.interaction['safe_alternative'],
                      color: AppColors.primary,
                    ),
                  ],
                  
                  // Emergency note
                  if (widget.interaction['emergency_note'] != null) ...[
                    const SizedBox(height: 12),
                    Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: AppColors.severityMajor.withOpacity(0.08),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(Icons.emergency, color: AppColors.severityMajor, size: 16),
                          const SizedBox(width: 6),
                          Expanded(child: Text(
                            widget.interaction['emergency_note'],
                            style: const TextStyle(fontSize: 12, 
                              color: AppColors.severityMajor),
                          )),
                        ],
                      ),
                    ),
                  ],
                  
                  const SizedBox(height: 12),
                  
                  // Disclaimer
                  Text(
                    'This is general information. Consult your doctor before making any changes.',
                    style: TextStyle(fontSize: 11, color: Colors.grey[500], 
                      fontStyle: FontStyle.italic),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  final String label;
  final String content;
  final IconData? iconData;
  final Color color;
  
  const _Section(this.label, this.content, 
    {this.iconData, this.color = Colors.black87});
  
  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: TextStyle(
          fontWeight: FontWeight.bold, fontSize: 13, color: color)),
        const SizedBox(height: 4),
        Text(content, style: const TextStyle(fontSize: 13, height: 1.5)),
      ],
    );
  }
}
```

## Screen 3: Schedule Timeline (lib/screens/schedule_screen.dart)

```dart
import 'package:flutter/material.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:intl/intl.dart';
import '../theme.dart';

class ScheduleScreen extends StatelessWidget {
  const ScheduleScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final today = DateFormat('yyyy-MM-dd').format(DateTime.now());
    
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: Text("Today's Schedule — ${DateFormat('d MMM').format(DateTime.now())}"),
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
      ),
      body: StreamBuilder<DocumentSnapshot>(
        stream: _scheduleStream(today),
        builder: (context, snapshot) {
          if (!snapshot.hasData || !snapshot.data!.exists) {
            return const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.schedule, size: 64, color: Colors.grey),
                  SizedBox(height: 16),
                  Text('No schedule yet.\nAdd medications and check interactions first.', 
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.grey)),
                ],
              ),
            );
          }
          
          final data = snapshot.data!.data() as Map<String, dynamic>;
          final doseTimes = List<Map<String, dynamic>>.from(data['dose_times'] ?? []);
          
          return ListView.separated(
            padding: const EdgeInsets.all(16),
            itemCount: doseTimes.length,
            separatorBuilder: (_, __) => const SizedBox(height: 8),
            itemBuilder: (context, index) {
              final dose = doseTimes[index];
              final meds = List<Map<String, dynamic>>.from(dose['medications'] ?? []);
              
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Timeline
                  Column(
                    children: [
                      Container(
                        width: 56,
                        padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 6),
                        decoration: BoxDecoration(
                          color: AppColors.primary,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(dose['time'] ?? '', 
                          style: const TextStyle(color: Colors.white, fontSize: 13,
                            fontWeight: FontWeight.bold),
                          textAlign: TextAlign.center),
                      ),
                      if (index < doseTimes.length - 1)
                        Container(width: 2, height: 40, color: Colors.grey[300]),
                    ],
                  ),
                  const SizedBox(width: 12),
                  
                  // Medications at this time
                  Expanded(
                    child: Card(
                      margin: EdgeInsets.zero,
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(dose['label'] ?? '', 
                              style: TextStyle(fontSize: 12, color: Colors.grey[600])),
                            const SizedBox(height: 8),
                            ...meds.map((med) => Padding(
                              padding: const EdgeInsets.only(bottom: 8),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      const Text('💊 ', style: TextStyle(fontSize: 16)),
                                      Expanded(child: Text(
                                        '${med['med_name']} — ${med['dose']}',
                                        style: const TextStyle(fontWeight: FontWeight.w600),
                                      )),
                                    ],
                                  ),
                                  if (med['instruction']?.isNotEmpty ?? false)
                                    Padding(
                                      padding: const EdgeInsets.only(left: 28),
                                      child: Text(med['instruction'], 
                                        style: TextStyle(fontSize: 12, color: Colors.grey[600])),
                                    ),
                                  if (med['interaction_warning'] != null)
                                    Padding(
                                      padding: const EdgeInsets.only(left: 28, top: 2),
                                      child: Row(
                                        children: [
                                          Icon(Icons.warning_amber_rounded, 
                                            size: 14, color: AppColors.severityModerate),
                                          const SizedBox(width: 4),
                                          Expanded(child: Text(
                                            med['interaction_warning'],
                                            style: TextStyle(fontSize: 11, 
                                              color: AppColors.severityModerate),
                                          )),
                                        ],
                                      ),
                                    ),
                                ],
                              ),
                            )),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              );
            },
          );
        },
      ),
    );
  }
}
```

## Deliverable Checklist
- [ ] HomeScreen shows interaction cards sorted by severity (contraindicated first)
- [ ] InteractionCard: tap to expand, shows severity badge with correct color
- [ ] InteractionCard: shows plain-language explanation, what to do, time gap note
- [ ] ScheduleScreen: timeline view with correct dose times
- [ ] SOS button appears on HomeScreen when critical interactions detected
- [ ] All screens use consistent AppColors system
- [ ] Medical disclaimer appears on all interaction detail views
- [ ] App looks clean and professional on a physical Android device at 1080x2340
