from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import BinaryIO


PLY_TYPES = {
    "char": "b",
    "uchar": "B",
    "int8": "b",
    "uint8": "B",
    "short": "h",
    "ushort": "H",
    "int16": "h",
    "uint16": "H",
    "int": "i",
    "uint": "I",
    "int32": "i",
    "uint32": "I",
    "float": "f",
    "float32": "f",
    "double": "d",
    "float64": "d",
}

VIEWER_SETTINGS_VERSION = 5
VIEWER_BOUNDS_TRIM_FRACTION = 0.01
VIEWER_BOUNDS_MAX_SAMPLES = 200_000
SPARSE_FOCUS_TRIM_FRACTION = 0.05
POINT3D_RECORD = struct.Struct("<QdddBBBdQ")
IMAGE_POSE_RECORD = struct.Struct("<I7dI")


def _uint64_header(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(8)
        return struct.unpack("<Q", data)[0] if len(data) == 8 else None
    except OSError:
        return None


def _sparse_model_dir(job_dir: Path) -> Path | None:
    candidates = (
        # GLOMAP writes numbered models directly under the requested output
        # directory; older/test COLMAP layouts may include sparse/.
        job_dir / "work" / "images_scaled" / "0",
        job_dir / "work" / "images_scaled" / "sparse" / "0",
        job_dir / "work" / "sparse" / "0",
    )
    return next((path for path in candidates if path.is_dir()), None)


def capture_diagnostics(job_dir: Path, uploaded_views: int) -> dict | None:
    """Read the cheap summary fields from a COLMAP binary sparse model."""
    model = _sparse_model_dir(job_dir)
    if model is None:
        return None
    registered = _uint64_header(model / "images.bin")
    points = _uint64_header(model / "points3D.bin")
    if registered is None and points is None:
        return None

    notes: list[str] = []
    levels: list[str] = []
    if registered is not None and uploaded_views:
        ratio = registered / uploaded_views
        levels.append("poor" if ratio < 0.70 else "limited" if ratio < 0.90 else "good")
        if ratio < 0.90:
            notes.append("low_view_registration")
    if points is not None:
        levels.append("poor" if points < 500 else "limited" if points < 2000 else "good")
        if points < 2000:
            notes.append("low_track_count")
    rank = {"good": 0, "limited": 1, "poor": 2}
    level = max(levels, key=rank.get) if levels else "good"
    return {
        "uploaded_views": uploaded_views,
        "registered_views": registered,
        # Sparse points with multi-view observations are the reliable tracks
        # that survived feature matching and camera registration.
        "reliable_tracks": points,
        "level": level,
        "notes": notes,
    }


def _trimmed_axis_bounds(values: list[float], trim_fraction: float) -> tuple[float, float]:
    values.sort()
    last = len(values) - 1
    low = math.floor(last * trim_fraction)
    high = math.ceil(last * (1 - trim_fraction))
    return values[low], values[high]


def sparse_subject_bounds(
    job_dir: Path,
    *,
    trim_fraction: float = SPARSE_FOCUS_TRIM_FRACTION,
    max_samples: int = VIEWER_BOUNDS_MAX_SAMPLES,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Use triangulated feature tracks as a camera-focus proxy for the subject."""
    model = _sparse_model_dir(job_dir)
    if model is None:
        return None
    path = model / "points3D.bin"
    try:
        with path.open("rb") as stream:
            count_data = stream.read(8)
            if len(count_data) != 8:
                return None
            count = struct.unpack("<Q", count_data)[0]
            sample_every = max(1, math.ceil(count / max_samples))
            axes: list[list[float]] = [[], [], []]
            for point_index in range(count):
                data = stream.read(POINT3D_RECORD.size)
                if len(data) != POINT3D_RECORD.size:
                    return None
                record = POINT3D_RECORD.unpack(data)
                position = record[1:4]
                track_length = record[-1]
                if track_length > 10_000_000:
                    return None
                stream.seek(track_length * 8, 1)
                if point_index % sample_every == 0 and all(math.isfinite(value) for value in position):
                    for axis, value in enumerate(position):
                        axes[axis].append(float(value))
    except OSError:
        return None
    if len(axes[0]) < 8:
        return None
    bounds = [_trimmed_axis_bounds(axis, trim_fraction) for axis in axes]
    return tuple(item[0] for item in bounds), tuple(item[1] for item in bounds)


def registered_camera_center(job_dir: Path) -> tuple[float, float, float] | None:
    """Return the mean COLMAP camera center in reconstruction coordinates."""
    model = _sparse_model_dir(job_dir)
    if model is None:
        return None
    centers: list[tuple[float, float, float]] = []
    try:
        with (model / "images.bin").open("rb") as stream:
            count_data = stream.read(8)
            if len(count_data) != 8:
                return None
            count = struct.unpack("<Q", count_data)[0]
            if count > 1_000_000:
                return None
            for _ in range(count):
                data = stream.read(IMAGE_POSE_RECORD.size)
                if len(data) != IMAGE_POSE_RECORD.size:
                    return None
                record = IMAGE_POSE_RECORD.unpack(data)
                qw, qx, qy, qz = record[1:5]
                tx, ty, tz = record[5:8]
                norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
                if not math.isfinite(norm) or norm < 1e-12:
                    return None
                qw, qx, qy, qz = (value / norm for value in (qw, qx, qy, qz))
                rotation = (
                    (1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)),
                    (2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)),
                    (2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)),
                )
                translation = (tx, ty, tz)
                center = tuple(
                    -sum(rotation[row][axis] * translation[row] for row in range(3))
                    for axis in range(3)
                )
                if all(math.isfinite(value) for value in center):
                    centers.append(center)
                for _ in range(4096):
                    byte = stream.read(1)
                    if not byte:
                        return None
                    if byte == b"\0":
                        break
                else:
                    return None
                points_data = stream.read(8)
                if len(points_data) != 8:
                    return None
                point_count = struct.unpack("<Q", points_data)[0]
                if point_count > 100_000_000:
                    return None
                stream.seek(point_count * 24, 1)
    except OSError:
        return None
    if not centers:
        return None
    return tuple(sum(center[axis] for center in centers) / len(centers) for axis in range(3))


def _viewer_coordinates(point: tuple[float, float, float]) -> tuple[float, float, float]:
    # The pinned SuperSplat viewer rotates imported splats 180 degrees around Z.
    return -point[0], -point[1], point[2]


def _viewer_bounds(
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    return (-maximum[0], -maximum[1], minimum[2]), (-minimum[0], -minimum[1], maximum[2])


def _read_ply_header(stream: BinaryIO) -> tuple[str, int, list[tuple[str, str]]]:
    first = stream.readline()
    if first.rstrip() != b"ply":
        raise ValueError("Not a PLY file")
    encoding = ""
    vertex_count = 0
    properties: list[tuple[str, str]] = []
    current_element = ""
    for _ in range(10000):
        raw = stream.readline()
        if not raw:
            raise ValueError("PLY header is incomplete")
        line = raw.decode("ascii", errors="strict").strip()
        fields = line.split()
        if fields[:1] == ["format"] and len(fields) >= 2:
            encoding = fields[1]
        elif fields[:1] == ["element"] and len(fields) == 3:
            current_element = fields[1]
            if current_element == "vertex":
                vertex_count = int(fields[2])
        elif fields[:1] == ["property"] and current_element == "vertex":
            if len(fields) != 3 or fields[1] not in PLY_TYPES:
                raise ValueError("Unsupported PLY vertex property")
            properties.append((fields[2], fields[1]))
        elif line == "end_header":
            break
    else:
        raise ValueError("PLY header is too large")
    if encoding not in {"ascii", "binary_little_endian"}:
        raise ValueError("Unsupported PLY encoding")
    names = {name for name, _ in properties}
    if vertex_count <= 0 or not {"x", "y", "z"}.issubset(names):
        raise ValueError("PLY has no positioned vertices")
    return encoding, vertex_count, properties


def ply_bounds(
    path: Path,
    *,
    trim_fraction: float = 0.0,
    max_samples: int = VIEWER_BOUNDS_MAX_SAMPLES,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return exact or robust position bounds with bounded sampling memory.

    Gaussian reconstructions often contain a few distant, translucent floaters.
    Exact extrema remain available for callers that need them, while the viewer
    requests percentile-trimmed bounds so those outliers do not make the useful
    reconstruction appear tiny.
    """
    if not 0 <= trim_fraction < 0.5:
        raise ValueError("trim_fraction must be between 0 and 0.5")
    if max_samples <= 0:
        raise ValueError("max_samples must be positive")
    with path.open("rb") as stream:
        encoding, count, properties = _read_ply_header(stream)
        indices = [next(i for i, item in enumerate(properties) if item[0] == axis) for axis in "xyz"]
        minimum = [math.inf, math.inf, math.inf]
        maximum = [-math.inf, -math.inf, -math.inf]
        samples: list[list[float]] = [[], [], []]
        sample_every = max(1, math.ceil(count / max_samples))

        def include(position: list[float], vertex_index: int) -> None:
            if not all(math.isfinite(value) for value in position):
                return
            for axis, value in enumerate(position):
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
                if trim_fraction and vertex_index % sample_every == 0:
                    samples[axis].append(value)

        if encoding == "binary_little_endian":
            row = struct.Struct("<" + "".join(PLY_TYPES[kind] for _, kind in properties))
            for vertex_index in range(count):
                data = stream.read(row.size)
                if len(data) != row.size:
                    raise ValueError("PLY vertex data is truncated")
                values = row.unpack(data)
                position = [float(values[index]) for index in indices]
                include(position, vertex_index)
        else:
            for vertex_index in range(count):
                fields = stream.readline().split()
                if len(fields) < len(properties):
                    raise ValueError("PLY vertex data is truncated")
                position = [float(fields[index]) for index in indices]
                include(position, vertex_index)
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        raise ValueError("PLY has no finite positioned vertices")
    if trim_fraction and len(samples[0]) >= 100:
        for axis in range(3):
            minimum[axis], maximum[axis] = _trimmed_axis_bounds(samples[axis], trim_fraction)
    return tuple(minimum), tuple(maximum)


def viewer_settings_for_ply(path: Path, *, job_dir: Path | None = None) -> dict:
    source_visible_bounds = ply_bounds(path, trim_fraction=VIEWER_BOUNDS_TRIM_FRACTION)
    visible_minimum, visible_maximum = _viewer_bounds(*source_visible_bounds)
    focus = sparse_subject_bounds(job_dir) if job_dir is not None else None
    minimum, maximum = _viewer_bounds(*focus) if focus else (visible_minimum, visible_maximum)
    target = [(low + high) / 2 for low, high in zip(minimum, maximum)]
    half_diagonal = math.sqrt(sum(((high - low) / 2) ** 2 for low, high in zip(minimum, maximum)))
    radius = max(half_diagonal, 0.01)
    fov = 55
    padding = 1.35 if focus else 1.12
    distance = radius / math.sin(math.radians(fov / 2)) * padding
    source_camera_center = registered_camera_center(job_dir) if job_dir is not None else None
    camera_center = _viewer_coordinates(source_camera_center) if source_camera_center is not None else None
    if camera_center is not None:
        direction = [camera_center[axis] - target[axis] for axis in range(3)]
        direction_length = math.sqrt(sum(value * value for value in direction))
    else:
        direction = [0.0, 0.0, -1.0]
        direction_length = 1.0
    if direction_length < 1e-12:
        direction = [0.0, 0.0, -1.0]
        direction_length = 1.0
        camera_center = None
    position = [target[axis] + direction[axis] / direction_length * distance for axis in range(3)]
    return {
        "version": 2,
        "tonemapping": "aces",
        "highPrecisionRendering": False,
        "background": {"color": [0.067, 0.067, 0.067]},
        "postEffectSettings": {
            "sharpness": {"enabled": False, "amount": 0},
            "bloom": {"enabled": False, "intensity": 0.1, "blurLevel": 2},
            "grading": {
                "enabled": False,
                "brightness": 1,
                "contrast": 1,
                "saturation": 1,
                "tint": [1, 1, 1],
            },
            "vignette": {"enabled": False, "intensity": 0.5, "inner": 0.3, "outer": 0.75, "curvature": 1},
            "fringing": {"enabled": False, "intensity": 0.5},
        },
        "animTracks": [],
        "cameras": [{"initial": {"position": position, "target": target, "fov": fov}}],
        "annotations": [],
        "startMode": "default",
        "photogrammetry": {
            "settingsVersion": VIEWER_SETTINGS_VERSION,
            "framing": "sparse_subject" if focus else "robust_bounds",
            "viewDirection": "registered_cameras" if camera_center is not None else "world_axis",
            "trimFraction": SPARSE_FOCUS_TRIM_FRACTION if focus else VIEWER_BOUNDS_TRIM_FRACTION,
            "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
            "visibleBounds": {"minimum": list(visible_minimum), "maximum": list(visible_maximum)},
        },
    }


def ensure_viewer_settings(results_dir: Path) -> Path | None:
    settings_path = results_dir / "viewer-settings.json"
    splats = sorted(
        (path for path in results_dir.rglob("*.ply") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    if not splats:
        return None
    source = splats[-1]
    if settings_path.exists() and settings_path.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            if existing.get("photogrammetry", {}).get("settingsVersion") == VIEWER_SETTINGS_VERSION:
                return settings_path
        except (OSError, ValueError, TypeError):
            pass
    try:
        payload = viewer_settings_for_ply(source, job_dir=results_dir.parent)
    except (OSError, ValueError, OverflowError):
        return None
    temporary = settings_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(settings_path)
    return settings_path
