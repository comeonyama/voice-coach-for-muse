#!/usr/bin/env python3
"""VCM finalize recovery: close out a run whose monitor died before its
finish sequence ran (e.g. the agent runtime restarted mid-run, as on
2026-11-03, leaving the queue on the run session with no standby switch
or cleanup).

What it does, only when session.json still says state=live:
  1. Build the closing message from poll_state.json's last point
     (distance / duration), never from zeros.
  2. Publish it, wait (default 60 s) so the app can fetch it,
     then switch the queue to standby and clean up run audio.
  3. Mark session.json as closed (state=closed, closed_by=finalize_run).

Safety: if the last track point is fresh (< --fresh-sec, default 120),
a monitor may still be alive; the tool refuses unless --force is given.
Use --dry-run to see exactly what would be sent.

Usage:
  finalize_run.py --config coach_config.json [--dry-run] [--force]
"""
import argparse
from runtime import private_dir, private_append, write_json, error_info, read_state, valid_session
import datetime
import json
import pathlib
import subprocess
import sys
import time

from coach_monitor import Monitor
from configuration import validate_config
from runtime import file_lock
from finish_run import finish_run, load_pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fresh-sec", type=int, default=120)
    ap.add_argument("--wait", type=int, default=60)
    args = ap.parse_args()

    cfg = validate_config(json.loads(pathlib.Path(args.config).read_text()))
    state = pathlib.Path(cfg["state_dir"])
    if args.dry_run:
        # Preview performs no quarantine, chmod or state writes.
        try:
            session = json.loads((state / "session.json").read_text())
        except (OSError, ValueError):
            print("dry-run: session unavailable")
            return 0
        print("dry-run: would resume closing, history, standby, cleanup" if session.get("state") == "live" else "nothing to do")
        return 0
    try:
        with file_lock(state / ".monitor.lock", blocking=False):
            return recover(cfg, args)
    except BlockingIOError:
        print("refusing: a monitor owns this state directory")
        return 4


def recover(cfg, args):
    state = pathlib.Path(cfg["state_dir"])
    session = read_state(state / "session.json", valid=valid_session)
    if not session or session.get("state") != "live":
        print("nothing to finalize")
        return 0
    progress = load_pending(cfg, session)
    monitor = Monitor(cfg, session, [], {})
    if progress:
        return 0 if finish_run(cfg, session, progress["text"], progress["reason"], monitor.log) else 2
    ps = read_state(state / "poll_state.json", {},
                    valid=lambda value: value.get("session_id") == session["session_id"]
                    and isinstance(value.get("last_point"), (dict, type(None))))
    ts = ps.get("last_point_ts")
    if ts and not args.force:
        try:
            timestamp = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age = (datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()
        except (ValueError, TypeError):
            age = None
        if age is not None and age < args.fresh_sec:
            print("refusing: last point is fresh; use --force after checking the monitor")
            return 4
    dist = monitor.last_dist
    avg = monitor.last_dur / dist if dist > 0.05 else None
    # Preserve --wait and its default on the recovery CLI.
    cfg = dict(cfg, close=dict(cfg.get("close") or {}, wait_before_standby_sec=args.wait))
    monitor.cfg = cfg
    return monitor.finish(dist, avg, "finalize_recovery")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({"ok": False, **error_info(error, "finalize")}))
        sys.exit(2)
