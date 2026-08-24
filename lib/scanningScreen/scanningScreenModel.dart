import 'dart:convert';
import 'dart:developer' as dev;
import 'dart:io';
import 'package:hexcolor/hexcolor.dart';
import 'package:path_provider/path_provider.dart';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:simple_photogrammetry_gui/main.dart';
import 'package:path/path.dart' as path;
import 'package:simple_photogrammetry_gui/utils/copyImageFolder.dart';
import 'dart:math';

import 'package:simple_photogrammetry_gui/utils/downloadResource.dart';
import 'package:simple_photogrammetry_gui/utils/extractZip.dart';
import 'package:simple_photogrammetry_gui/utils/getCoreCountForMemoryIntensiveTaks.dart';
import 'package:simple_photogrammetry_gui/utils/removeSpacesFromFileNames.dart';
import 'package:simple_photogrammetry_gui/utils/renameDirectory.dart';
import 'package:simple_photogrammetry_gui/utils/renameFile.dart';

class ScanningScreenModel {
  ScanningScreenModel({this.executableDirectory});

  final String? executableDirectory;

  List runningProcesses = [];

  var view;

  String _getAppDir() {
    final configuredExecutableDirectory = executableDirectory;
    if (configuredExecutableDirectory != null) {
      return configuredExecutableDirectory;
    }
    final appDir = Platform.environment['APPDIR'];
    if (appDir != null) {
      return "$appDir/usr/bin";
    } else {
      return '/workspace/install/bin';
    }
  }

  showAlert(
    ColorScheme colorScheme,
    BuildContext context,
    String title,
    List<Widget> buttons, {
    String? desc,
    Widget? content,
    double height = 100,
  }) {
    var alert = AlertDialog(
      backgroundColor: HexColor("#282828"),
      title: ConstrainedBox(child: Text(title, style: TextStyle(color: HexColor("#ebdbb2")),textAlign: TextAlign.center,),constraints: BoxConstraints(maxWidth: 600),),
      titlePadding: EdgeInsets.all(16),
      content: Column(
        mainAxisAlignment: MainAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          desc != null ? Text(desc, style: TextStyle(color: HexColor("#ebdbb2"))) : Container(),
          content ?? Container(),
          Row(mainAxisAlignment: MainAxisAlignment.center, children: buttons),
        ],
      ),
    );
    showDialog(context: context, builder: (_) => alert);
  }

  _prepFolders(String outputPath) async {
    await Directory(path.join(outputPath, "temp", "sparse")).create(recursive: true);
    await Directory(path.join(outputPath, "temp", "dense", "sparse")).create(recursive: true);
    await File(path.join(outputPath, "temp", "database.db")).create(recursive: true);
  }

  scanningProcess(
    var view,
    String imagesPath,
    String outputPath,
    int qualityLevel,
    bool photogrammetry_or_splat,
  ) async {

    this.view = view;

    final directory = (await getApplicationSupportDirectory()).path;

    if ((await checkDependencies(view))) {
      String appDir = _getAppDir();

      // Get Dependency Folders on Windows & Linux
      String colmapPath = Platform.isWindows
          ? path.join(directory, "colmap", "bin", "colmap.exe")
          : path.join(appDir, "colmap");

      String brushPath = Platform.isWindows
          ? path.join(directory, "brush", "brush_app.exe")
          : path.join(appDir, "brush", "brush_app");

      String openMvsPath = Platform.isWindows
          ? path.join(directory, 'openMVS')
          : path.join(appDir, 'OpenMVS');

      String decimateMeshPath = Platform.isWindows
          ? path.join(directory, 'decimateMesh.exe')
          : path.join(appDir, 'decimateMesh');

      String poissonRecon = Platform.isWindows
          ? path.join(directory, 'PoissonRecon.exe')
          : path.join(appDir, 'PoissonRecon');

      String surfaceTrimmer = Platform.isWindows
          ? path.join(directory, 'SurfaceTrimmer.exe')
          : path.join(appDir, 'SurfaceTrimmer');

      String mvsTexturing = Platform.isWindows
          ? path.join(directory, 'texrecon', 'texrecon.exe')
          : path.join(appDir, 'texrecon');

      String fastDownscaler = Platform.isWindows
          ? path.join(directory, 'fast_downscaler', 'fast_downscaler.exe')
          : path.join(appDir, 'fast_downscaler');

      String databasePath = path.join(outputPath, 'temp', 'database.db');
      String glomapDatabasePath = path.join(outputPath, 'temp', 'global_database.db');

      int totalStepNumber = 10;

      if (photogrammetry_or_splat) {
        totalStepNumber = 4;
      }

      await _prepFolders(outputPath);

      List maximagesizes = [-1, 3000, 2000];

      final resizedImagePath = path.join(outputPath, "temp", "images_scaled");

      await Directory(resizedImagePath).create(recursive: true);

      if (maximagesizes[qualityLevel] != -1) {
        view.status = "0/$totalStepNumber Copy & Resize Images";
        view.setState(() {});

        int threads = max(1, Platform.numberOfProcessors - 1);

        await runCommand(fastDownscaler, [
          imagesPath,
          resizedImagePath,
          maximagesizes[qualityLevel].toString(),
          threads.toString(),
        ]);

        
      }else{

        view.status = "0/$totalStepNumber Copy Images";
        view.setState(() {});

        await copyImageFolder(imagesPath, resizedImagePath, () {
          return view.stop;
        });

      }

      removeSpacesFromFileNames(resizedImagePath,() {
          return view.stop;
      });

      imagesPath = resizedImagePath;

      final modelColmapFile = File(path.join(outputPath, "temp", "model_colmap.mvs"));

      if (!(await modelColmapFile.exists())) {
        view.status = "1/$totalStepNumber Sift Extraction";
        view.setState(() {});

        String featureExtractorThreads = global_max_cpu_threads;

        if(gpu_cpu_type == "cpu" && global_max_cpu_threads == "-1") {
          featureExtractorThreads = (await calculateRamIntensiveCoreCount()).toString();
        }

        if(gpu_cpu_type == "cpu") {

          view.logs = "Using $featureExtractorThreads threads for Feature Extraction (-1 = All)\n${view.logs}";
          view.setState(() {});

        }

        await runCommand(colmapPath, [
          "feature_extractor",
          "--database_path",
          databasePath,
          "--image_path",
          imagesPath,
          "--FeatureExtraction.use_gpu",
          "${view.useGpu ? 1 : 0}",
          "--FeatureExtraction.num_threads",
          featureExtractorThreads,
        ]);

        view.status = "2/$totalStepNumber SiftMatching";
        view.setState(() {});

        await runCommand(colmapPath, [
          feature_matching_type,
          "--FeatureMatching.use_gpu",
          "${view.useGpu ? 1 : 0}",
          "--database_path",
          databasePath,
          "--FeatureMatching.num_threads",
          global_max_cpu_threads,
        ]);

        view.status = "3/$totalStepNumber Aligning Cameras with Glomap";
        view.setState(() {});

        final sourceFile = File(databasePath);

        if (await sourceFile.exists()) {
          await sourceFile.copy(glomapDatabasePath);
        }

        await runCommand(colmapPath, [
          "view_graph_calibrator",
          "--database_path",
          glomapDatabasePath,
        ]);
      }

      if (photogrammetry_or_splat) {
        await runCommand(colmapPath, [
          "global_mapper",
          "--database_path",
          glomapDatabasePath,
          "--image_path",
          imagesPath,
          "--output_path",
          imagesPath,
        ]);

        view.status = "4/$totalStepNumber Training Splat";
        view.setState(() {});

        await runCommand(brushPath, [
          imagesPath,
          "--export-path",
          outputPath,
          "--total-steps",
          splat_training_steps,
        ], workingFolder: outputPath);

        view.status = "Done";
        view.setState(() {});
        return;
      } else {
        if (!(await modelColmapFile.exists())) {
          await runCommand(colmapPath, [
            "global_mapper",
            "--database_path",
            glomapDatabasePath,
            "--image_path",
            imagesPath,
            "--output_path",
            path.join(outputPath, "temp", "sparse"),
          ]);

          view.status = "4/$totalStepNumber Undistorting Images";
          view.setState(() {});

          await runCommand(colmapPath, [
            "image_undistorter",
            "--image_path",
            imagesPath,
            "--input_path",
            path.join(outputPath, "temp", "sparse", "0"),
            "--output_path",
            path.join(outputPath, "temp", "dense"),
            "--output_type",
            "COLMAP",
          ]);

          view.status = "5.1/$totalStepNumber Converting Project";
          view.setState(() {});

          await runCommand(colmapPath, [
            "model_converter",
            "--input_path",
            path.join(outputPath, "temp", "dense", "sparse"),
            "--output_path",
            path.join(outputPath, "temp", "dense", "sparse"),
            "--output_type",
            "TXT",
          ]);

          view.status = "5.2/$totalStepNumber Converting Project";
          view.setState(() {});

          await runCommand(colmapPath, [
            "model_converter",
            "--input_path",
            path.join(outputPath, "temp", "dense", "sparse"),
            "--output_path",
            path.join(imagesPath, "project.nvm"),
            "--output_type",
            "NVM",
          ]);

          view.status = "6/$totalStepNumber Converting Project to OpenMVS";
          view.setState(() {});

          await runCommand(path.join(openMvsPath, "InterfaceCOLMAP"), [
            "--working-folder",
            path.join(outputPath, "temp", "dense"),
            "--input-file",
            path.join(outputPath, "temp", "dense"),
            "--output-file",
            path.join(outputPath, "temp", "model_colmap.mvs"),
          ]);
        }
      }

      List denseQualityLevels = [2560, 1920, 1024];

      int maxImgResolution = denseQualityLevels[qualityLevel];
      int denseRetrys = 1;

      while (!File(path.join(outputPath, "temp", "model_dense.mvs")).existsSync()) {
        if (denseRetrys == 1) {
          view.status = "7/$totalStepNumber Densifying Point Cloud";
          view.setState(() {});
        } else {
          view.status =
              "7/$totalStepNumber Densifying Point Cloud failed, retrying with a max image resolution of $maxImgResolution";
        }
        view.setState(() {});

        if (Platform.isWindows) {
          final tempDirectory = Directory(path.join(outputPath, "temp"));
          await for (final entry in tempDirectory.list()) {
            if (entry is File && path.extension(entry.path) == ".dmap") {
              await entry.delete();
            }
          }
        }

        await runCommand(path.join(openMvsPath, "DensifyPointCloud"), [
          "--input-file", path.join(outputPath, "temp", "model_colmap.mvs"),
          "--working-folder", path.join(outputPath, "temp"),
          "--output-file", path.join(outputPath, "temp", "model_dense.mvs"),
          "--max-resolution", maxImgResolution.toString(),
          //  "--crop-to-roi", "0",
          "--roi-border", "10",
        ]);

        if (denseRetrys == 5) {
          view.status = "Failed, went wrong at DensifyPointCloud";
          //view.running = false;
          view.setState(() {});
          return;
        }
        denseRetrys++;
        maxImgResolution = (maxImgResolution * 0.7).floor();
      }

      List mesh_recon_quality_levels = [12, 11, 10];

      double decimationFactorMeshRecon = 1;
      int meshReconRetrys = 1;

      while (!File(path.join(outputPath, "temp", "model_surface.mvs")).existsSync() &&
          !File(path.join(outputPath, "temp", "model_surface.ply")).existsSync()) {
        if (decimationFactorMeshRecon == 1.0) {
          view.status = "9/$totalStepNumber Reconstructing Mesh";
          view.setState(() {});
        } else {
          view.status =
              "9/$totalStepNumber Reconstructing Mesh failed, retrying with decimation factor $meshReconRetrys";
          view.setState(() {});
        }

        if (meshing_type == "poissonrecon") {
          List<String> poissonReconArguments = [
            "--in",
            path.join(outputPath, "temp", "model_dense.ply"),
            "--out",
            path.join(outputPath, "temp", "model_surface.ply"),
            "--depth",
            "${mesh_recon_quality_levels[qualityLevel]}",
            "--density",
          ]..addAll(poissonExtraFlags.split(" "));

          await runCommand(poissonRecon, poissonReconArguments);
        } else {
          await runCommand(
            path.join(openMvsPath, "ReconstructMesh"),
            [
              "--input-file",
              path.join(outputPath, "temp", "model_dense.mvs"),
              "--working-folder",
              path.join(outputPath, "temp"),
              "--output-file",
              "model_surface.mvs",
              "-d",
              (2.0 + (double.parse(meshReconRetrys.toString()) / 2)).toString(),
            ]..addAll(meshingExtraFlags.split(" ")),
          );
        }

        if (meshReconRetrys == 10) {
          view.status = "Failed, went wrong at Mesh Reconstruction";
          view.setState(() {});
          return;
        }
        meshReconRetrys++;
      }

      view.status = "9/$totalStepNumber Reconstructing Mesh";
      view.setState(() {});

      if (meshing_type == "poissonrecon") {
        List<String> surfaceTrimerArguments = [
          "--in",
          path.join(outputPath, "temp", "model_surface.ply"),
          "--out",
          path.join(outputPath, "temp", "model_surface_cleaned.ply"),
          "--trim",
          "4",
          "--ascii",
        ]..addAll(surfaceTrimmerExtraFlags.split(" "));

        await runCommand(surfaceTrimmer, surfaceTrimerArguments);

        List decimationLevels = [0.01, 0.03, 0.1];

        List<String> decimateMeshArgs = [
          "-m",
          path.join(outputPath, "temp", "model_surface_cleaned.ply"),
          "-o",
          path.join(outputPath, "temp"),
          "-t",
          decimationLevels[qualityLevel].toString(),
        ];

        await runCommand(decimateMeshPath, decimateMeshArgs);
      }

      view.status = "10/$totalStepNumber Texturing Mesh";
      view.setState(() {});

      try {
        await runCommand(mvsTexturing, [
          "--keep_unseen_faces",
          path.join(imagesPath, "project.nvm"),
          path.join(
            outputPath,
            "temp",
            "${meshing_type == "poissonrecon" ? "model_surface_decimated" : "model_surface"}.ply",
          ),
          path.join(outputPath, "textured"),
        ], workingFolder: path.join(outputPath, "temp", "dense", "images"));
      } catch (_) {}

      if (!File(path.join(outputPath, "textured.obj")).existsSync()) {
        view.status = "Failed, went wrong at texturing mesh";
        //view.running = false;
        view.setState(() {});
        return;
      }

      view.status = "Done";
      view.setState(() {});
    }
  }

  runCommand(String command, List<String> attr, {String? workingFolder}) async {
    if (view.stop) {
      view.status = "";
      view.setState(() {});
      throw "User Stopped Scanning Process";
    }

    try{

    var process = await Process.start(command, attr, workingDirectory: workingFolder);
    runningProcesses.add(process);

    process.stdout.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
      if (view != null) {
        view.logs = "$line\n${view.logs}";
        view.setState(() {});
      }
    });

    process.stderr.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
      if (view != null) {
        view.logs = "$line\n${view.logs}";
        view.setState(() {});
      }
    });

    final exitCode = await process.exitCode;

    runningProcesses.remove(process);

    }catch(e) {
      view.logs = "Flutter Error: $e\n${view.logs}";
    }

    return exitCode;
  }

  stop(var view) {
    for (final process in runningProcesses) {
      process.kill(ProcessSignal.sigkill);
    }
    runningProcesses.clear();
    view.stop = true;
    view.status = "";
    view.setState(() {});
  }

  downloadWrapper(var view,String computeType) async {
    Navigator.pop(view.context);

            view.isDownloadingDependencies = true;
            view.setState(() {});

            String status = await downloadDependencies(view, computeType);

            if(status != "") {

              showAlert(view.colorScheme, view.context, "Dependecy Download Failed:\n $status",[
                  TextButton(
                    onPressed: () async {
                      Navigator.pop(view.context);
                      downloadWrapper(view,computeType);
                    },
                    child: Text(
                      "Retry",
                      style: TextStyle(color: HexColor("#ebdbb2"), fontSize: 18),
                    ),
                  ),
              ]);

            }

            gpu_cpu_type = computeType;
            if(computeType == "cpu") {
              view.useGpu = false;
              view.setState(() {});
            }

            if (Platform.isWindows) {
              final SharedPreferences prefs = await SharedPreferences.getInstance();

              prefs.setString("gpu_cpu_type", computeType);
            }

            view.isDownloadingDependencies = false;
            view.setState(() {});
  }

  dependencyAlert(var view,String title) {
    showAlert(view.colorScheme, view.context, title, [
        Platform.isLinux
            ? Container()
            : TextButton(
                onPressed: () {
                  downloadWrapper(view,"cuda");
                },
                child: Text(
                  "Yes (CUDA / NVIDIA)",
                  style: TextStyle(color: HexColor("#ebdbb2"), fontSize: 18),
                ),
              ),
        TextButton(
          onPressed: () {
            downloadWrapper(view,"cpu");
          },
          child: Text(
            "Yes${Platform.isLinux ? "" : " (CPU Only)"}",
            style: TextStyle(color: HexColor("#ebdbb2"), fontSize: 18),
          ),
        ),
        TextButton(
          onPressed: () async {
            showAlert(
              view.colorScheme,
              view.context,
              "AMD GPU Support is Beta, if something does not work, please don't hesitate to open an issue on Github",
              [
                TextButton(
                  onPressed: () {
                    Navigator.pop(view.context);

                    downloadWrapper(view,"amd");
                  },
                  child: Text(
                    "Understood",
                    style: TextStyle(color: HexColor("#ebdbb2"), fontSize: 18),
                  ),
                ),
              ],
            );
          },
          child: Text(
            "Yes${Platform.isLinux ? "" : " (HIP / AMD)"}",
            style: TextStyle(color: HexColor("#ebdbb2"), fontSize: 18),
          ),
        ),
      ]);
  }

  checkDependencies(var view) async {
    final directory = (await getApplicationSupportDirectory()).path;

    print("Directory: $directory");

    bool hasAllDependencies = false;
    if (Platform.isWindows) {
      bool hasColmap = await Directory(path.join(directory, "colmap")).exists();
      bool hasOpenMVS = await Directory(path.join(directory, "openMVS")).exists();
      bool hasTexRecon = await File(path.join(directory, "texrecon", "texrecon.exe")).exists();
      bool hasResizeImages = await File(path.join(directory, "resizeImages.exe")).exists();
      bool hasDecimateMesh = await File(path.join(directory, "decimateMesh.exe")).exists();
      bool hasTextureMesh = await File(path.join(directory, "textureMesh.exe")).exists();
      bool hasPoissonRecon = await File(path.join(directory, "PoissonRecon.exe")).exists();
      bool hasSurfaceTrimmer = await File(path.join(directory, "SurfaceTrimmer.exe")).exists();
      bool fastDownscaler = await File(
        path.join(directory, "fast_downscaler", "fast_downscaler.exe"),
      ).exists();
      bool hasBrush = await File(path.join(directory, "brush", "brush_app.exe")).exists();

      hasAllDependencies =
          hasColmap &&
          hasOpenMVS &&
          hasTexRecon &&
          hasResizeImages &&
          hasDecimateMesh &&
          hasTextureMesh &&
          hasPoissonRecon &&
          hasSurfaceTrimmer &&
          hasBrush &&
          fastDownscaler;
    } else if (Platform.isLinux) {
      hasAllDependencies = true;
    }

    if ((await SharedPreferences.getInstance()).getString("did_download") == "V1") {
      hasAllDependencies = true;
    }

    if (!hasAllDependencies) {
      dependencyAlert(view,"Some dependencies are missing, download them now?");
    }
    return hasAllDependencies;
  }

  downloadDependencies(var view, String computeType) async {
    final directory = (await getApplicationSupportDirectory()).path;

    dev.log("Directory: $directory");

    if (Platform.isWindows) {
      if (!directory.contains('simple_photogrammetry_gui')) {
        return 'Path $directory does not contain simple_photogrammetry_gui';
      } else {
        final Directory _directory = Directory(directory);

        if (await _directory.exists()) {
          try{
            await _directory.delete(recursive: true);
          }catch(e) {
            return e.toString();
          }
        }

        await _directory.create(recursive: true);
      }

      try{

      if (computeType != "amd") {
        await downloadResource(
          "https://github.com/colmap/colmap/releases/download/4.0.4/${computeType == "cuda" ? "colmap-x64-windows-cuda.zip" : "colmap-x64-windows-nocuda.zip"}",
          "colmap.zip",
          (line) {
            view.logs = "Colmap $line\n${view.logs}";
            view.setState(() {});
          }
        );
      }

      await downloadResource(
        "https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-pc-windows-msvc.zip",
        "brush.zip",
        (line) {
            view.logs = "Brush $line\n${view.logs}";
            view.setState(() {});
        }
      );

      view.logs = "Extracting...\n${view.logs}";
      view.setState(() {});

      if (computeType != "amd") {
        await extractZip("colmap.zip", path.join(directory, "colmap"));
      } else {
        await extractZip("colmap_amd.zip", path.join(directory, "colmap"));
      }

      await extractZip("brush.zip", path.join(directory, "brush"));

      await extractZip("decimateMesh.zip", directory);

      await extractZip("resizeImages.zip", directory);

      await extractZip("textureMesh.zip", directory);

      await extractZip("texrecon.zip", directory);

      await extractZip("PoissonRecon.zip", directory);

      await extractZip("fast_downscaler.zip", directory);

      if (computeType != "amd") {
        await renameDirectory(
          path.join(
            directory,
            "colmap",
            computeType == "cuda" ? 'colmap-x64-windows-cuda' : 'colmap-x64-windows-nocuda',
          ),
          path.join(directory, "colmap", "colmap"),
        );
      }

      await renameFile(
        computeType == "cuda"
            ? 'openmvs_cuda.zip'
            : (computeType == "amd" ? "openmvs_amd.zip" : 'openmvs_no_cuda.zip'),
        "openmvs.zip",
      );

      await extractZip("openmvs.zip", path.join(directory, "openMVS"));

      }catch(e, stackTrace) {
        view.logs = "Error Donwloading: $e\n$stackTrace\n${view.logs}";
        view.setState(() {});
        dev.log("Error Donwloading: $e\n$stackTrace");
        return e.toString();
      }

      (await SharedPreferences.getInstance()).setString("did_download", "V1");
    }
    return "";
  }

  permissionErrorAlert(var view) {
    showAlert(view.colorScheme, view.context, "Permission Error - Permission Denied", [
      TextButton(
        onPressed: () {
          Navigator.pop(view.context);
        },
        child: Text("Ok", style: TextStyle(color: HexColor("#ebdbb2"))),
      ),
    ]);
  }
}
