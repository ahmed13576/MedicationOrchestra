import 'package:flutter/material.dart';

class AboutScreen extends StatelessWidget {
  const AboutScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('About Medication Orchestra'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Center(
            child: Column(
              children: [
                Icon(
                  Icons.medical_services,
                  size: 64,
                  color: Color(0xFF1A73E8),
                ),
                SizedBox(height: 16),
                Text(
                  'Medication Orchestra',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                ),
                Text(
                  'Version 1.0.0',
                  style: TextStyle(color: Colors.grey),
                ),
                SizedBox(height: 24),
              ],
            ),
          ),
          Card(
            child: ExpansionTile(
              leading: const Icon(Icons.description_outlined, color: Color(0xFF1A73E8)),
              title: const Text('Licenses & Open Source'),
              children: [
                Padding(
                  padding: const EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'This application uses open-source software, including components licensed under the Apache License, Version 2.0.',
                        style: TextStyle(fontSize: 13),
                      ),
                      const SizedBox(height: 12),
                      ElevatedButton(
                        onPressed: () {
                          showLicensePage(
                            context: context,
                            applicationName: 'Medication Orchestra',
                            applicationVersion: '1.0.0',
                            applicationIcon: const Icon(
                              Icons.medical_services,
                              color: Color(0xFF1A73E8),
                            ),
                          );
                        },
                        child: const Text('View All Open Source Licenses'),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          const Card(
            child: ExpansionTile(
              leading: Icon(Icons.gavel_outlined, color: Colors.orange),
              title: Text('Medical Non-Liability Disclaimer'),
              children: [
                Padding(
                  padding: EdgeInsets.all(16.0),
                  child: Text(
                    'DISCLAIMER: This application is powered by Gemini and Vertex AI to check drug-to-drug interactions and generate medication schedules. However, all generated information is for educational purposes only.\n\n'
                    'It does NOT constitute professional medical advice, diagnosis, or treatment. Never disregard professional medical advice or delay seeking it because of something you have read or generated in this app.\n\n'
                    'Always consult a qualified healthcare provider or physician before starting, stopping, or changing any medication dosage, timing, or schedule.',
                    style: TextStyle(fontSize: 13, height: 1.4),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          const Card(
            child: ExpansionTile(
              leading: Icon(Icons.person_outline, color: Color(0xFF1A73E8)),
              title: Text('Developer Information'),
              children: [
                Padding(
                  padding: EdgeInsets.all(16.0),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Developer: Mohammed Ahmed',
                        style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                      ),
                      SizedBox(height: 8),
                      Text(
                        'Email: m13576ahmed@gmail.com',
                        style: TextStyle(fontSize: 14),
                      ),
                      SizedBox(height: 12),
                      Text(
                        'Feel free to reach out for feedback, issues, or suggestions regarding the Medication Orchestra app.',
                        style: TextStyle(fontSize: 13, color: Colors.grey),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
