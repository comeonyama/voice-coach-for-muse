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
            with patch.object(watcher, "publisher_call", return_value={"ok": False, "returncode": 1}), \
                 patch.object(watcher.subprocess, "Popen") as popen:
                self.assertFalse(w.launch(session, False))
                popen.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_timeout_reaches_fallback(self):
        import delivery
        with tempfile.TemporaryDirectory() as directory:
            cfg = dict(state_dir=directory, poll_script="fixture", publish_script="fixture",
                       say_retry_backoff_sec=0, say_attempts=2)
            monitor = Monitor(cfg, {}, [], {})
            failed = {"ok": False, "stage": "say", "error_type": "TimeoutExpired"}
            with patch("coach_monitor.publisher_call", side_effect=[failed, failed, {"ok": True}]) as call:
                self.assertTrue(monitor.say("fixture"))
                self.assertEqual([c.args[1] for c in call.call_args_list], ["say", "say", "post"])
                self.assertEqual(len({c.args[2] for c in call.call_args_list}), 1)
        with patch.object(delivery.subprocess, "Popen", side_effect=OSError("secret")):
            result = delivery.publisher_call(cfg, "post", "id", "text")
            self.assertFalse(result["ok"])
            self.assertNotIn("secret", json.dumps(result))

    def test_idempotent_pending_queue(self):
        import importlib
        import types
        credentials = types.ModuleType("dynamic_credentials")
        credentials.add_surrogate_to_request = lambda *args, **kwargs: None
        credentials.read_json_response = lambda response: None
        with patch.dict(sys.modules, {"dynamic_credentials": credentials}):
            pub = importlib.import_module("publish")
        with tempfile.TemporaryDirectory() as directory, patch.object(pub, "BASE", directory), \
             contextlib.redirect_stdout(io.StringIO()):
            pub.cmd_init("session")
            with patch.object(pub, "upload_queue", return_value=(503, {"ok": False})):
                pub.cmd_post("id", "text")
            self.assertEqual(len(pub.load_queue()["messages"]), 1)
            stamp = pub.load_queue()["messages"][0]["ts"]
            with patch.object(pub, "upload_queue", return_value=(200, {"ok": True})):
                pub.cmd_post("id", "retry")
                pub.cmd_post("other", "next")
            queue = pub.load_queue()
            self.assertEqual([m["id"] for m in queue["messages"]], ["id", "other"])
            self.assertEqual(queue["messages"][0]["ts"], stamp)


def parallel_post(directory, index):
    import types
    credentials = types.ModuleType("dynamic_credentials")
    credentials.add_surrogate_to_request = lambda *args, **kwargs: None
    credentials.read_json_response = lambda response: None
    sys.modules["dynamic_credentials"] = credentials
    import publish
    publish.BASE = directory
    publish.upload_queue = lambda queue: (200, {"ok": True})
    with contextlib.redirect_stdout(io.StringIO()):
        publish.cmd_post(str(index), "fixture")


class ConcurrentPublisherTests(unittest.TestCase):
    def test_parallel_posts_preserve_all_ids(self):
        import multiprocessing
        import types
        import importlib
        credentials = types.ModuleType("dynamic_credentials")
        credentials.add_surrogate_to_request = lambda *args, **kwargs: None
        credentials.read_json_response = lambda response: None
        with patch.dict(sys.modules, {"dynamic_credentials": credentials}):
            pub = importlib.import_module("publish")
        with tempfile.TemporaryDirectory() as directory, patch.object(pub, "BASE", directory):
            with contextlib.redirect_stdout(io.StringIO()):
                pub.cmd_init("fixture")
            ctx = multiprocessing.get_context("fork")
            processes = [ctx.Process(target=parallel_post, args=(directory, i)) for i in range(8)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual({m["id"] for m in pub.load_queue()["messages"]}, {str(i) for i in range(8)})
