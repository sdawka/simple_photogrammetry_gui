import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.__main__ import _configure_runtime_cache


class MainTests(unittest.TestCase):
    def test_runtime_cache_defaults_beneath_persistent_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with patch.dict(os.environ, {}, clear=True):
                cache = _configure_runtime_cache(state)
                self.assertEqual(state / "cache", cache)
                self.assertEqual(str(cache), os.environ["XDG_CACHE_HOME"])
                self.assertTrue(cache.is_dir())

    def test_explicit_cache_environment_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            explicit = Path(temporary) / "models-cache"
            with patch.dict(
                os.environ,
                {"PHOTOGRAMMETRY_CACHE_DIR": str(explicit), "XDG_CACHE_HOME": "/existing"},
                clear=True,
            ):
                self.assertEqual(explicit, _configure_runtime_cache(state))
                self.assertEqual("/existing", os.environ["XDG_CACHE_HOME"])


if __name__ == "__main__":
    unittest.main()
