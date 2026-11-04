"""Resumable end-of-run delivery shared by monitor and recovery command."""
import datetime
import json
import os
from pathlib import Path
import time

from delivery import publish_retry
from runtime import file_lock, read_state, write_json, valid_session

STAGES = ("closing", "history", "wait", "standby", "sync", "cleanup", "closed")


def load_pending(cfg, session):
    path = Path(cfg["state_dir"]) / "finish_state.json"
    value = read_state(path, {}, valid=lambda doc:
                       doc.get("stage") in STAGES
                       and all(isinstance(doc.get(key), str)
                               for key in ("session_id", "queue_session", "text", "reason"))
                       and isinstance(doc.get("wait_until"), (int, float)))
    if value.get("session_id") == session["session_id"]:
        return value
    return None


def finish_run(cfg, session, text, reason, log):
    state = Path(cfg["state_dir"])
    checkpoint = state / "finish_state.json"
    queue_dir = Path(os.environ.get("VCM_QUEUE_DIR", str(Path.home() / "vcm/queue")))
    queue_path = Path(cfg.get("queue_json") or queue_dir / "queue.json")
    with file_lock(state / ".finish.lock"):
        progress = load_pending(cfg, session)
        if progress is None:
            # Missing/invalid queue is not permission to overwrite server state.
            try:
                queue = json.loads(queue_path.read_text())
                queue_session = queue["session"]
                if not isinstance(queue_session, str) or queue_session == (cfg.get("close") or {}).get("standby_session", "standby"):
                    raise ValueError("run queue unavailable")
            except (OSError, ValueError, KeyError, TypeError):
                log({"event": "finish_pending", "stage": "queue_snapshot"})
                return False
            progress = dict(session_id=session["session_id"], queue_session=queue_session,
                            text=text, reason=reason, stage="closing", wait_until=0)
            write_json(checkpoint, progress)

        while progress["stage"] != "closed":
            stage = progress["stage"]
            standby = (cfg.get("close") or {}).get("standby_session", "standby")
            allowed_sessions = {progress["queue_session"]}
            if stage == "standby":
                allowed_sessions.add(standby)  # init succeeded before checkpoint was saved
            elif stage in ("sync", "cleanup"):
                allowed_sessions = {standby}
            try:
                queue = json.loads(queue_path.read_text())
                if queue.get("session") not in allowed_sessions:
                    raise ValueError("queue belongs to another run")
            except (OSError, ValueError, AttributeError):
                log({"event": "finish_pending", "stage": "queue_changed"})
                return False
            if stage == "closing":
                verb = "say" if cfg.get("audio", True) else "post"
                attempts = int(cfg.get("closing_attempts", 4))
                result = publish_retry(cfg, verb, "run-final", progress["text"], attempts=attempts)
                if not result["ok"] and verb == "say" and cfg.get("tts_fallback", True):
                    result = publish_retry(cfg, "post", "run-final", progress["text"], attempts=attempts)
                if result["ok"]:
                    progress["wait_until"] = time.time() + (cfg.get("close") or {}).get("wait_before_standby_sec", 60)
            elif stage == "history":
                result = publish_retry(cfg, "history-append")
            elif stage == "wait":
                time.sleep(max(0, progress["wait_until"] - time.time()))
                result = {"ok": True}
            else:
                commands = {
                    "standby": ("init", (cfg.get("close") or {}).get("standby_session", "standby")),
                    "sync": ("sync",),
                    "cleanup": ("cleanup", "0"),
                }
                result = publish_retry(cfg, *commands[stage])
            log({"event": "finish_stage", "stage": stage, "ok": result["ok"]})
            if not result["ok"]:
                return False
            progress["stage"] = STAGES[STAGES.index(stage) + 1]
            write_json(checkpoint, progress)

        current = read_state(state / "session.json", {}, valid=valid_session)
        if current.get("session_id") == session["session_id"]:
            current["state"] = "closed"
            current["closed_by"] = "finalize_run" if progress["reason"] == "finalize_recovery" else "coach_monitor"
            current["closed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            write_json(state / "session.json", current)
        log({"event": "finished", "reason": progress["reason"]})
        return True
