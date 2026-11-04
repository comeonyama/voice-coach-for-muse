import contextlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configuration import validate_config
from runtime import write_json, error_info
from coach_monitor import Monitor


class BaselineTests(unittest.TestCase):
    def test_sample_and_invalid_config(self):
        cfg = json.loads((Path(__file__).parents[1] / "config.sample.json").read_text())
        validate_config(cfg)
        with tempfile.TemporaryDirectory() as directory:
            cfg["state_dir"] = directory
            monitor = Monitor(cfg, {}, [], {})
            self.assertIsInstance(monitor.hr_judgment(125), str)
        for key, value in (("hr_bands", {}), ("say_attempts", 0), ("audio", "false"),
                           ("hydration", {"every_min": 0})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "config"):
                validate_config(dict(cfg, **{key: value}))

    def test_permissions_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "session.json"
            write_json(path, {"token": "synthetic-secret"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        error = subprocess.TimeoutExpired(["poll", "--token", "synthetic-secret"], 60)
        self.assertNotIn("synthetic-secret", json.dumps(error_info(error, "poll")))
