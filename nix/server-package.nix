{
  lib,
  stdenvNoCC,
  makeWrapper,
  fetchurl,
  brush-splat,
  python3,
  callPackage,
  colmapPackage,
  openmvsPackage,
  mvsTexturingSource,
  mapmapSource,
  rayintSource,
  mveSource,
  poissonReconSource,
  supersplatViewerSource,
  fastDownscaler,
}:

let
  aliked-n16rot-model = fetchurl {
    url = "https://github.com/colmap/colmap/releases/download/3.13.0/aliked-n16rot.onnx";
    hash = "sha256-OcQj0KbwPTnsidPR1hhTdlwvtqi4OBN2xwPldYd4pUc=";
  };
  aliked-lightglue-model = fetchurl {
    url = "https://github.com/colmap/colmap/releases/download/3.13.0/aliked-lightglue.onnx";
    hash = "sha256-uaXecgRkixioz13KyBn50w3hpZYe8DdWgDyLhsLc640=";
  };
  mvs-texturing = callPackage ./mvs-texturing.nix {
    inherit
      mvsTexturingSource
      mapmapSource
      rayintSource
      mveSource
      ;
  };
  poisson-recon = callPackage ./poisson-recon.nix { inherit poissonReconSource; };
  pythonEnv = python3.withPackages (pythonPackages: [ pythonPackages.pymeshlab ]);
in
stdenvNoCC.mkDerivation {
  pname = "simple-photogrammetry-server-cuda-sm61";
  version = "1.2.0";

  src = lib.cleanSource ../.;
  nativeBuildInputs = [ makeWrapper pythonEnv ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONPATH=. ${lib.getExe pythonEnv} -m unittest discover \
      -s server -p 'test*.py'
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p \
      "$out/bin" \
      "$out/lib/photogrammetry-server" \
      "$out/share/photogrammetry-server/models" \
      "$out/usr/bin/OpenMVS" \
      "$out/usr/bin/brush"

    cp -R server "$out/lib/photogrammetry-server/server"
    cp ${aliked-n16rot-model} \
      "$out/share/photogrammetry-server/models/aliked-n16rot.onnx"
    cp ${aliked-lightglue-model} \
      "$out/share/photogrammetry-server/models/aliked-lightglue.onnx"

    mkdir -p "$out/lib/photogrammetry-server/server/static/viewer"
    cp \
      ${supersplatViewerSource}/public/index.html \
      ${supersplatViewerSource}/public/index.css \
      ${supersplatViewerSource}/public/index.js \
      "$out/lib/photogrammetry-server/server/static/viewer/"
    cp ${supersplatViewerSource}/LICENSE \
      "$out/lib/photogrammetry-server/server/static/viewer/LICENSE"

    ln -s ${lib.getExe colmapPackage} "$out/usr/bin/colmap"
    ln -s ${lib.getExe brush-splat} "$out/usr/bin/brush/brush_app"
    ln -s ${lib.getExe mvs-texturing} "$out/usr/bin/texrecon"
    ln -s ${poisson-recon}/bin/PoissonRecon "$out/usr/bin/PoissonRecon"
    ln -s ${poisson-recon}/bin/SurfaceTrimmer "$out/usr/bin/SurfaceTrimmer"
    ln -s ${lib.getExe fastDownscaler} "$out/usr/bin/fast_downscaler"

    for program in InterfaceCOLMAP DensifyPointCloud ReconstructMesh; do
      ln -s "${openmvsPackage}/bin/$program" "$out/usr/bin/OpenMVS/$program"
    done

    install -Dm644 python/decimateMesh.py \
      "$out/share/photogrammetry-server/decimateMesh.py"
    makeWrapper ${lib.getExe pythonEnv} "$out/usr/bin/decimateMesh" \
      --add-flags "$out/share/photogrammetry-server/decimateMesh.py"

    makeWrapper ${lib.getExe pythonEnv} "$out/bin/photogrammetry-server" \
      --add-flags "-m server" \
      --set PYTHONPATH "$out/lib/photogrammetry-server" \
      --set PHOTOGRAMMETRY_BIN_DIR "$out/usr/bin" \
      --set PHOTOGRAMMETRY_WEB_DIR "$out/lib/photogrammetry-server/server/static"

    runHook postInstall
  '';

  meta = {
    description = "Headless photogrammetry and Gaussian-splat job service for GTX 1060 GPUs";
    homepage = "https://github.com/sdawka/simple_photogrammetry_gui";
    license = lib.licenses.gpl3Only;
    mainProgram = "photogrammetry-server";
    platforms = [ "x86_64-linux" ];
  };
}
