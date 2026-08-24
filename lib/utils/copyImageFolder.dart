import 'dart:io';

import 'package:path/path.dart' as path;

copyImageFolder(String sourcePath, String destPath, bool Function() shouldStop) async {
  final sourceDir = Directory(sourcePath);
        final destDir = Directory(destPath);

        const imageExtensions = {'.png', '.jpg', '.jpeg', '.webp', '.heic', '.bmp'};

        await for (final FileSystemEntity entity in sourceDir.list(recursive: false)) {
          
          if(shouldStop()) {
            throw "User Stopped Scanning Process";
            return;
          }

          if (entity is File) {
            final extension = path.extension(entity.path).toLowerCase();

            if (imageExtensions.contains(extension)) {
              final fileName = path.basename(entity.path);
              final destinationFilePath = path.join(destDir.path, fileName);

              // Copy file to the new path (overwrites existing files by default)
              await entity.copy(destinationFilePath);
            }
          }
        }
}