import 'dart:io';

/// Detects total system memory in Gigabytes (GB).
Future<double> getSystemTotalMemoryInGB() async {
  try {
    if (Platform.isLinux) {
      final file = File('/proc/meminfo');
      final lines = await file.readAsLines();
      for (final line in lines) {
        if (line.startsWith('MemTotal:')) {
          final parts = line.split(RegExp(r'\s+'));
          final memoryInKB = double.parse(parts[1]);
          return memoryInKB / (1024 * 1024);
        }
      }
    } else if (Platform.isMacOS) {
      final result = await Process.run('sysctl', ['-n', 'hw.memsize']);
      if (result.exitCode == 0) {
        final memoryInBytes = double.parse(result.stdout.toString().trim());
        return memoryInBytes / (1024 * 1024 * 1024);
      }
    } else if (Platform.isWindows) {
      final result = await Process.run('wmic', ['OS', 'get', 'TotalVisibleMemorySize']);
      if (result.exitCode == 0) {
        final lines = result.stdout.toString().trim().split('\n');
        if (lines.length > 1) {
          final memoryInKB = double.parse(lines[1].trim());
          return memoryInKB / (1024 * 1024);
        }
      }
    }
  } catch (e) {
    // Fallback if command fails
  }
  return 8.0; // Default conservative fallback (8 GB)
}

/// Calculates the safe number of CPU threads to allocate for COLMAP SIFT extraction.
Future<int> calculateRamIntensiveCoreCount({
  double ramPerThreadGB = 2.0,
  double reservedRamGB = 2.5, 
}) async {
  final totalRamGB = await getSystemTotalMemoryInGB();
  final availableCpuCores = Platform.numberOfProcessors;

  final usableRam = totalRamGB - reservedRamGB;
  if (usableRam <= 0) return 1;

  // Max threads supported by available RAM
  final maxThreadsByRam = (usableRam / ramPerThreadGB).floor();

  // Clamp thread count between 1 and total available CPU cores
  return maxThreadsByRam.clamp(1, availableCpuCores);
}