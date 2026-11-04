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

    def test_restart_sequence_preserves_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            path.write_text(json.dumps({"messages": [
                {"id": "run-001"}, {"id": "run-007"}, {"id": "run-final"}]}))
            monitor = Monitor.__new__(Monitor)
            monitor.cfg = {"queue_json": str(path)}
            self.assertEqual(monitor._initial_seq(), 7)

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


class FinishTests(unittest.TestCase):
    def test_resume_each_failed_stage_without_erasing_history(self):
        import finish_run as finish
        for failure in ("closing", "history-append", "init", "sync", "cleanup"):
            with self.subTest(stage=failure), tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state"
                queue = Path(directory) / "queue" / "queue.json"
                cfg = dict(state_dir=str(state), queue_json=str(queue),
                           close={"wait_before_standby_sec": 0},
                           audio=True, say_retry_backoff_sec=0, publish_script="fixture")
                session = {"session_id": "sid", "token": "fake", "state": "live"}
                write_json(state / "session.json", session)
                write_json(queue, {"session": "run-fixture", "messages": []})
                calls = []
                failing = True

                def publish(cfg, *args, **kwargs):
                    command = args[0]
                    calls.append(command)
                    fail = failing and (command == failure or failure == "closing" and command in ("say", "post"))
                    if command == "init" and not fail:
                        write_json(queue, {"session": args[1], "messages": []})
                    return {"ok": not fail}

                with patch.object(finish, "publish_retry", side_effect=publish):
                    self.assertFalse(finish.finish_run(cfg, session, "closing", "ended", lambda event: None))
                    self.assertEqual(json.loads((state / "session.json").read_text())["state"], "live")
                    if failure in ("closing", "history-append"):
                        self.assertNotIn("init", calls)
                        self.assertEqual(json.loads(queue.read_text())["session"], "run-fixture")
                    calls.clear()
                    failing = False
                    self.assertTrue(finish.finish_run(cfg, session, "closing", "ended", lambda event: None))
                    if failure != "closing":
                        self.assertNotIn("say", calls)
                    self.assertEqual(json.loads((state / "session.json").read_text())["state"], "closed")
                    calls.clear()
                    self.assertTrue(finish.finish_run(cfg, session, "closing", "ended", lambda event: None))
                    self.assertEqual(calls, [])

    def test_history_merge_without_local_copy_and_read_failure(self):
        import importlib
        import types
        credentials = types.ModuleType("dynamic_credentials")
        credentials.add_surrogate_to_request = lambda *args, **kwargs: None
        credentials.read_json_response = lambda response: None
        with patch.dict(sys.modules, {"dynamic_credentials": credentials}):
            pub = importlib.import_module("publish")
        remote = {"runs": [{"session": "run-2026-11-01", "date": "2026-11-01",
                            "messages": [{"id": "old", "ts": "2026-11-01", "text": "old"}]}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(pub, "BASE", directory), \
             contextlib.redirect_stdout(io.StringIO()):
            write_json(Path(directory) / "queue.json",
                       {"session": "run-2026-11-04", "messages": [{"id": "new", "ts": "2026-11-04", "text": "new"}]})
            with patch.object(pub, "remote_history", return_value=remote), \
                 patch.object(pub, "post", return_value=(200, {"ok": True})) as post:
                pub.cmd_history_append()
                history = json.loads(post.call_args.args[1])
                self.assertEqual([run["session"] for run in history["runs"]],
                                 ["run-2026-11-04", "run-2026-11-01"])
            with patch.object(pub, "remote_history", side_effect=OSError("offline")), \
                 patch.object(pub, "post") as post:
                with self.assertRaises(OSError):
                    pub.cmd_history_append()
                post.assert_not_called()

    def test_finalize_dry_run_is_read_only(self):
        import finalize_run
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            session = {"session_id": "sid", "token": "fake", "state": "live"}
            write_json(state / "session.json", session)
            cfg = dict(state_dir=str(state), poll_script="fixture", publish_script="fixture")
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(cfg))
            with patch.object(sys, "argv", ["finalize", "--config", str(config_path), "--dry-run"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(finalize_run.main(), 0)
            self.assertEqual(json.loads((state / "session.json").read_text()), session)
            self.assertFalse((state / "finish_state.json").exists())

    def test_recovery_without_points_honors_text_mode(self):
        import finalize_run
        import finish_run as finish
        import types
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            queue = Path(directory) / "queue" / "queue.json"
            cfg = dict(state_dir=str(state), queue_json=str(queue), poll_script="fixture",
                       publish_script="fixture", audio=False)
            write_json(state / "session.json", {"session_id": "sid", "token": "fake", "state": "live"})
            write_json(queue, {"session": "run-fixture", "messages": []})
            calls = []

            def publish(cfg, *args, **kwargs):
                calls.append(args[0])
                if args[0] == "init":
                    write_json(queue, {"session": args[1], "messages": []})
                return {"ok": True}

            with patch.object(finish, "publish_retry", side_effect=publish):
                result = finalize_run.recover(cfg, types.SimpleNamespace(force=False, fresh_sec=120, wait=0))
            self.assertEqual(result, 0)
            self.assertEqual(calls, ["post", "history-append", "init", "sync", "cleanup"])
