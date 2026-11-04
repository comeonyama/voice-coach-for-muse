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


class StateTests(unittest.TestCase):
    def test_corrupt_state_quarantined(self):
        from runtime import read_state
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{")
            self.assertEqual(read_state(path, {}), {})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(directory).glob("*.corrupt-*"))), 1)
            self.assertEqual(read_state(path, {}), {})
            write_json(path, {"state": "live"})
            self.assertEqual(read_state(path)["state"], "live")

    def test_single_owner_lock(self):
        from runtime import file_lock, lock_busy
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.lock"
            with file_lock(path):
                self.assertTrue(lock_busy(path))
            self.assertFalse(lock_busy(path))


class WatcherTests(unittest.TestCase):
    def test_restart_and_initialization_failure(self):
        import importlib
        import types
        credentials = types.ModuleType("dynamic_credentials")
        credentials.add_surrogate_to_request = lambda *args, **kwargs: None
        credentials.read_json_response = lambda response: None
        with patch.dict(sys.modules, {"dynamic_credentials": credentials}):
            watcher = importlib.import_module("session_watcher")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cfg = {"state_dir": directory, "publish_script": "fixture"}
            (path / "config.json").write_text(json.dumps(cfg))
            args = types.SimpleNamespace(config=str(path / "config.json"),
                                         monitor="fixture", dry_run=False)
            w = watcher.Watcher(args)
            session = {"session_id": "s", "token": "fake", "state": "live"}
            write_json(path / "session.json", session)
            with patch.object(watcher, "WATCHER_STATE", path / "watcher.json"), \
                 patch.object(watcher, "fetch_session", return_value=(200, session)), \
                 patch.object(w, "launch", return_value=True) as launch:
                watcher.save_watcher_state({"launched_session_id": "s", "status": "running", "retries": 0})
                w.monitor_proc = types.SimpleNamespace(poll=lambda: 2)
                for _ in range(4):
                    w.cycle()
                self.assertEqual(launch.call_count, 3)
                self.assertTrue(all(call.kwargs["resume"] for call in launch.call_args_list))
            with patch.object(watcher.subprocess, "run", return_value=types.SimpleNamespace(returncode=1, stdout='{"ok":false}')), \
                 patch.object(watcher.subprocess, "Popen") as popen:
                self.assertFalse(w.launch(session, False))
                popen.assert_not_called()
