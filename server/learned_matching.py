from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


COLMAP_MAX_IMAGE_ID = 2_147_483_647
NEARBY_PAIR_RADIUS = 2
BRIDGE_BATCH_SIZE = 32
MAX_BRIDGE_PAIRS = 512
MIN_VERIFIED_INLIERS = 15


@dataclass(frozen=True)
class DatabaseImage:
    image_id: int
    name: str


Pair = tuple[int, int]


def normalize_pair(first: int, second: int) -> Pair:
    if first == second:
        raise ValueError("An image cannot be paired with itself")
    return (first, second) if first < second else (second, first)


def decode_pair_id(pair_id: int) -> Pair:
    second = pair_id % COLMAP_MAX_IMAGE_ID
    first = (pair_id - second) // COLMAP_MAX_IMAGE_ID
    return normalize_pair(first, second)


def read_database_images(database: Path) -> list[DatabaseImage]:
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(
                "SELECT image_id, name FROM images ORDER BY name, image_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not read learned feature database: {exc}") from exc
    return [DatabaseImage(int(image_id), str(name)) for image_id, name in rows]


def verified_pairs(database: Path) -> set[Pair]:
    """Return normally verified image pairs from a COLMAP database."""
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(
                """
                SELECT pair_id FROM two_view_geometries
                WHERE rows >= ? AND config != 0
                """,
                (MIN_VERIFIED_INLIERS,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Could not inspect learned match verification: {exc}") from exc
    return {decode_pair_id(int(row[0])) for row in rows}


def connected_components(image_ids: Iterable[int], edges: Iterable[Pair]) -> list[set[int]]:
    adjacency = {image_id: set() for image_id in image_ids}
    for first, second in edges:
        if first in adjacency and second in adjacency:
            adjacency[first].add(second)
            adjacency[second].add(first)
    components: list[set[int]] = []
    remaining = set(adjacency)
    while remaining:
        seed = min(remaining)
        component = {seed}
        frontier = [seed]
        remaining.remove(seed)
        while frontier:
            current = frontier.pop()
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    frontier.append(neighbor)
        components.append(component)
    return sorted(components, key=lambda component: (-len(component), min(component)))


def nearby_pairs(
    images: list[DatabaseImage], radius: int = NEARBY_PAIR_RADIUS
) -> list[Pair]:
    pairs: list[Pair] = []
    for index, image in enumerate(images):
        for neighbor in images[index + 1:index + radius + 1]:
            pairs.append(normalize_pair(image.image_id, neighbor.image_id))
    return pairs


def bridge_candidates(
    images: list[DatabaseImage],
    components: list[set[int]],
    attempted: set[Pair],
    *,
    limit: int,
) -> list[Pair]:
    """Prioritize chronologically close pairs that cross verified components."""
    component_for = {
        image_id: component_index
        for component_index, component in enumerate(components)
        for image_id in component
    }
    candidates: list[tuple[tuple[int, int, int, str, str], Pair]] = []
    for first_index, first in enumerate(images):
        for second_index in range(first_index + 1, len(images)):
            second = images[second_index]
            pair = normalize_pair(first.image_id, second.image_id)
            if (
                pair in attempted
                or component_for[first.image_id] == component_for[second.image_id]
            ):
                continue
            priority = (
                second_index - first_index,
                second_index,
                first_index,
                first.name,
                second.name,
            )
            candidates.append((priority, pair))
    candidates.sort(key=lambda item: item[0])
    return [pair for _, pair in candidates[:limit]]


def write_pair_file(
    path: Path, images: list[DatabaseImage], pairs: Iterable[Pair]
) -> None:
    names = {image.image_id: image.name for image in images}
    lines = [f"{names[first]} {names[second]}\n" for first, second in pairs]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(path)


def attempted_pairs_from_files(
    directory: Path, images: list[DatabaseImage]
) -> set[Pair]:
    ids = {image.name: image.image_id for image in images}
    attempted: set[Pair] = set()
    if not directory.exists():
        return attempted
    for path in sorted(directory.glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in ids and parts[1] in ids:
                attempted.add(normalize_pair(ids[parts[0]], ids[parts[1]]))
    return attempted
