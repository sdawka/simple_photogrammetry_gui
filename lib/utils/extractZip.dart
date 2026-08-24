import 'dart:io';
import 'dart:isolate';
import 'package:archive/archive_io.dart';

Future<void> extractZip(String filePathToUnpack, String outDirectory) async {
  await Isolate.run(() async {
    final zipFile = File(filePathToUnpack);

    if (zipFile.existsSync()) {
      await extractFileToDisk(zipFile.path, outDirectory);
    }
  });
}