import 'dart:io';

import 'package:path/path.dart' as path;

removeSpacesFromFileNames(String folderPath, bool Function() shouldStop) async {
  final dir = Directory(folderPath);

  if (!await dir.exists()) return;

  await for (final FileSystemEntity entity in dir.list(recursive: false)) {
    
    if (shouldStop()) {
      throw "User Stopped Scanning Process";
      return;
    }

    if (entity is File) {
      final fileName = path.basename(entity.path);

      if (fileName.contains(' ')) {
        final newFileName = fileName.replaceAll(' ', '_');
        final newPath = path.join(dir.path, newFileName);

        
        await entity.rename(newPath);
      }
    }
  }
}