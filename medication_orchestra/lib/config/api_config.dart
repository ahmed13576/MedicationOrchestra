/// API configuration for Medication Orchestra backend.
///
/// For physical device testing, run with:
///   flutter run --dart-define=API_BASE_URL=http://YOUR_LAN_IP:8080
///
/// Find your LAN IP with: ipconfig | findstr "IPv4"
/// Example: flutter run --dart-define=API_BASE_URL=http://192.168.1.42:8080
///
/// For Android emulator, the default 10.0.2.2 maps to host machine's localhost.
class ApiConfig {
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8080', // Android emulator → host localhost
  );
}
