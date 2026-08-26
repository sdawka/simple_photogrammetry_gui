import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from server.learned_matching import (
    COLMAP_MAX_IMAGE_ID,
    DatabaseImage,
    bridge_candidates,
    connected_components,
    nearby_pairs,
    attempted_pairs_from_files,
    verified_pairs,
    write_pair_file,
)


class LearnedMatchingTests(unittest.TestCase):
    def test_resume_ignores_uncommitted_pending_pair_file(self):
        images = [DatabaseImage(1, "01.png"), DatabaseImage(2, "02.png")]
        with tempfile.TemporaryDirectory() as temporary:
            pair_dir = Path(temporary)
            write_pair_file(pair_dir / "nearby.pending", images, [(1, 2)])
            self.assertEqual(set(), attempted_pairs_from_files(pair_dir, images))
            (pair_dir / "nearby.pending").replace(pair_dir / "nearby.txt")
            self.assertEqual({(1, 2)}, attempted_pairs_from_files(pair_dir, images))

    def test_nearby_pairs_are_adjacent_and_plus_two(self):
        images = [DatabaseImage(index, f"{index:02d}.png") for index in range(1, 6)]
        self.assertEqual(
            [(1, 2), (1, 3), (2, 3), (2, 4), (3, 4), (3, 5), (4, 5)],
            nearby_pairs(images),
        )

    def test_bridge_candidates_cross_components_and_respect_attempts(self):
        images = [DatabaseImage(index, f"{index:02d}.png") for index in range(1, 6)]
        components = [{1, 2, 3}, {4, 5}]
        attempted = {(3, 4)}
        candidates = bridge_candidates(images, components, attempted, limit=3)
        self.assertEqual([(2, 4), (3, 5), (1, 4)], candidates)
        self.assertTrue(all((first <= 3 < second) for first, second in candidates))

    def test_nine_plus_two_split_exhausts_only_cross_component_bridges(self):
        images = [DatabaseImage(index, f"{index:02d}.png") for index in range(1, 12)]
        attempted = set(nearby_pairs(images))
        candidates = bridge_candidates(
            images,
            [set(range(1, 10)), {10, 11}],
            attempted,
            limit=32,
        )
        self.assertEqual(15, len(candidates))
        self.assertIn((2, 11), candidates)
        self.assertIn((6, 10), candidates)
        self.assertTrue(all(first <= 9 < second for first, second in candidates))

    def test_only_normal_verified_geometry_connects_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "database.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE two_view_geometries("
                    "pair_id INTEGER PRIMARY KEY, rows INTEGER, config INTEGER)"
                )
                connection.executemany(
                    "INSERT INTO two_view_geometries VALUES (?, ?, ?)",
                    [
                        (1 * COLMAP_MAX_IMAGE_ID + 2, 15, 2),
                        (2 * COLMAP_MAX_IMAGE_ID + 3, 14, 2),
                        (3 * COLMAP_MAX_IMAGE_ID + 4, 100, 0),
                    ],
                )
            self.assertEqual({(1, 2)}, verified_pairs(database))
            self.assertEqual(
                [{1, 2}, {3}, {4}],
                connected_components(range(1, 5), {(1, 2)}),
            )


if __name__ == "__main__":
    unittest.main()
