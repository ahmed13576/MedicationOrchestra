import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medication_orchestra/screens/login_screen.dart';

/// Widget tests run without Firebase: LoginScreen only touches FirebaseAuth when
/// a button is pressed, so it can be pumped as-is.
///
/// The previous version of this file pumped the whole app, whose root widget
/// immediately reaches for FirebaseAuth.instance. That throws with no Firebase
/// app configured, so the test could not pass in a clean CI checkout.
void main() {
  testWidgets('LoginScreen renders its sign-in affordances', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: LoginScreen()));
    await tester.pump();

    expect(find.textContaining('Medication Orchestra'), findsWidgets);
    expect(find.byType(TextFormField), findsNWidgets(2));
    expect(find.text('Continue as Guest'), findsOneWidget);
  });

  testWidgets('LoginScreen exposes a guest path without leaving the screen',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(home: LoginScreen()));
    await tester.pump();

    // The guest path exists and is tappable; the tap itself needs Firebase,
    // which is why it is not exercised here.
    expect(find.text('Continue as Guest'), findsOneWidget);
    expect(tester.widget<OutlinedButton>(find.ancestor(
      of: find.text('Continue as Guest'),
      matching: find.byType(OutlinedButton),
    )).onPressed, isNotNull);
  });
}
