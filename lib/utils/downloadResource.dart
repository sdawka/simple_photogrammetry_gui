import 'dart:io';
import 'package:http/http.dart' as http;

downloadResource(String inUrl, String outName, Function(String log) updateProgress) async {
  final file = File(outName);

  if (!file.existsSync()) {
    final url = Uri.parse(inUrl);

    try {
      final request = http.Request('GET', url);
      final response = await http.Client().send(request);

      if (response.statusCode == 200) {
        final totalBytes = response.contentLength ?? 0;
        int receivedBytes = 0;

        final sink = file.openWrite();

        double lastProgress = 0.0;

        await for (final chunk in response.stream) {
          sink.add(chunk);
          receivedBytes += chunk.length;

          if (totalBytes > 0) {
            final progress = (receivedBytes / totalBytes) * 100;
            if((progress - lastProgress) >= 2) {

              lastProgress = progress;

              updateProgress('Download progress: ${progress.toStringAsFixed(1)}%');

            }
          }
        }

        await sink.flush();
        await sink.close();
        print('Download complete!');
      } else {
        throw HttpException('Failed to download file. Status: ${response.statusCode}');
      }
    } catch (e) {
      if (file.existsSync()) {
        file.deleteSync();
      }
    }
  }
}
