import tempfile
import unittest
from pathlib import Path

from server.store import JobStore


class JobStoreTests(unittest.TestCase):
    def test_fifo_claim_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JobStore(Path(temporary))
            first = store.create_job(name="first", kind="mesh", quality="medium", settings={})
            second = store.create_job(name="second", kind="splat", quality="medium", settings={})
            store.queue(first["id"])
            store.queue(second["id"])

            claimed = store.claim_next()
            self.assertEqual(first["id"], claimed["id"])
            self.assertEqual("running", claimed["state"])
            self.assertEqual(1, store.recover_after_restart())
            self.assertEqual("queued", store.get_job(first["id"])["state"])

            # Recovered work retains its original queue position.
            claimed_again = store.claim_next()
            self.assertEqual(first["id"], claimed_again["id"])


if __name__ == "__main__":
    unittest.main()
