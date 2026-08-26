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


def _uint64_header(path: Path) -> int | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(8)
        return struct.unpack("<Q", data)[0] if len(data) == 8 else None
    except OSError:
        return None


def capture_diagnostics(job_dir: Path, uploaded_views: int) -> dict | None:
    """Read the cheap summary fields from a COLMAP binary sparse model."""
    candidates = (
        job_dir / "work" / "images_scaled" / "sparse" / "0",
        job_dir / "work" / "sparse" / "0",
    )
    model = next((path for path in candidates if path.is_dir()), None)
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


def ply_bounds(path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    with path.open("rb") as stream:
        encoding, count, properties = _read_ply_header(stream)
        indices = [next(i for i, item in enumerate(properties) if item[0] == axis) for axis in "xyz"]
        minimum = [math.inf, math.inf, math.inf]
        maximum = [-math.inf, -math.inf, -math.inf]
        if encoding == "binary_little_endian":
            row = struct.Struct("<" + "".join(PLY_TYPES[kind] for _, kind in properties))
            for _ in range(count):
                data = stream.read(row.size)
                if len(data) != row.size:
                    raise ValueError("PLY vertex data is truncated")
                values = row.unpack(data)
                position = [float(values[index]) for index in indices]
                if not all(math.isfinite(value) for value in position):
                    continue
                for axis, value in enumerate(position):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
        else:
            for _ in range(count):
                fields = stream.readline().split()
                if len(fields) < len(properties):
                    raise ValueError("PLY vertex data is truncated")
                position = [float(fields[index]) for index in indices]
                if not all(math.isfinite(value) for value in position):
                    continue
                for axis, value in enumerate(position):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
    if not all(math.isfinite(value) for value in (*minimum, *maximum)):
        raise ValueError("PLY has no finite positioned vertices")
    return tuple(minimum), tuple(maximum)


def viewer_settings_for_ply(path: Path) -> dict:
    minimum, maximum = ply_bounds(path)
    target = [(low + high) / 2 for low, high in zip(minimum, maximum)]
    half_diagonal = math.sqrt(sum(((high - low) / 2) ** 2 for low, high in zip(minimum, maximum)))
    radius = max(half_diagonal, 0.01)
    fov = 55
    distance = radius / math.sin(math.radians(fov / 2)) * 1.12
    position = [target[0], target[1], target[2] - distance]
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
            "framing": "bounds",
            "bounds": {"minimum": list(minimum), "maximum": list(maximum)},
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
        return settings_path
    try:
        payload = viewer_settings_for_ply(source)
    except (OSError, ValueError, OverflowError):
        return None
    temporary = settings_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(settings_path)
    return settings_path
