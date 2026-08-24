import 'dart:io';

Future<void> renameDirectory(String oldDirPath, String newDirPath) async {
  final oldDir = Directory(oldDirPath);
  final newDir = Directory(newDirPath);

  if (oldDir.existsSync()) {
    await oldDir.rename(newDir.path);
  }
}
