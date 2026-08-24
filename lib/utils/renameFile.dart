import 'dart:io';

renameFile(String oldFileName, String newFileName) async {
  final oldFile = File(oldFileName);
  final newFile = File(newFileName);

  if (oldFile.existsSync()) {
    await oldFile.rename(newFile.path);
  }
}
