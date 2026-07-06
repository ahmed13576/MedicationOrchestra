import 'package:flutter_test/flutter_test.dart';
import 'package:medication_orchestra/main.dart';

void main() {
  testWidgets('HomeScreen renders app title', (WidgetTester tester) async {
    // Firebase.initializeApp() is called in main() but not in tests.
    // We pump the app widget directly without full Firebase init.
    await tester.pumpWidget(const MedicationOrchestraApp());
    await tester.pump();

    // Verify the app title is present
    expect(find.text('Medication Orchestra'), findsWidgets);
  });
}
