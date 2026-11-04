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
from runtime import private_dir, private_append, write_json, error_info
import datetime
import json
import pathlib
import subprocess
import sys
import time

DIGITS_JP = {"0": "ゼロ", "1": "いち", "2": "に", "3": "さん", "4": "よん",
             "5": "ご", "6": "ろく", "7": "なな", "8": "はち", "9": "きゅう"}


def num_jp(n):
    if n < 10:
        return DIGITS_JP[str(n)]
    t, o = divmod(n, 10)
    s = (DIGITS_JP[str(t)] if t > 1 else "") + "じゅう"
    if o:
        s += DIGITS_JP[str(o)]
    return s


def dist_spoken(d):
    d = round(d, 1)
    i = int(d)
    f = int(round((d - i) * 10))
    if f == 10:
        i += 1
        f = 0
    if f == 0:
        return num_jp(i)
    return f"{num_jp(i)}点{DIGITS_JP[str(f)]}"


def pace_str(spk):
    if not spk or spk <= 0:
        return "―"
    m = int(spk // 60)
    s = int(round(spk % 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}分{s:02d}秒"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fresh-sec", type=int, default=120)
    ap.add_argument("--wait", type=int, default=60)
    args = ap.parse_args()

    cfg = json.loads(pathlib.Path(args.config).read_text())
    state = pathlib.Path(cfg["state_dir"])
    sess_path = state / "session.json"
    sess = json.loads(sess_path.read_text())
    if sess.get("state") != "live":
        print(f"nothing to do: session state is {sess.get('state')!r}")
        return 0
    ps_path = state / "poll_state.json"
    ps = json.loads(ps_path.read_text()) if ps_path.exists() else {}
    lp = ps.get("last_point") or {}
    dist = float(lp.get("dist_m") or 0) / 1000.0
    dur = lp.get("dur_s") or 0
    if dist <= 0.05:
        # No data to recover a closing from. Still close the state:
        # leaving it "live" makes the session watcher retry forever and
        # blocks the next run's automatic start.
        sess["state"] = "closed"
        sess["closed_by"] = "finalize_run"
        sess["closed_at"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
        write_json(sess_path, sess)
        print("no track points recorded; session state closed without a closing message")
        return 0
    ts = ps.get("last_point_ts")
    if ts and not args.force:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
        if age < args.fresh_sec:
            print(f"refusing: last point is only {int(age)}s old; a monitor may still be running (use --force)")
            return 4
    avg = dur / dist
    closing = cfg.get("closing_text",
                      "{distance}キロ。平均ペースはキロ{pace}でした。おつかれさまでした。")
    text = closing.replace("{distance}", dist_spoken(dist)).replace("{pace}", pace_str(avg))
    print(f"closing: {text}  (dist={dist:.2f} km, dur={dur}s)")

    def publish(*a):
        r = subprocess.run(["python3", cfg["publish_script"], *a],
                           capture_output=True, text=True, timeout=120)
        return '"ok": true' in (r.stdout or "")

    if args.dry_run:
        print("dry-run: would say the closing, wait, init standby, sync, cleanup, mark closed")
        return 0
    ok = publish("say", "run-final", text)
    print("closing published:", ok)
    if args.wait:
        time.sleep(args.wait)
    standby = (cfg.get("close") or {}).get("standby_session", "standby")
    publish("init", standby)
    publish("sync")
    publish("cleanup", "0")
    sess["state"] = "closed"
    sess["closed_by"] = "finalize_run"
    sess["closed_at"] = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
    write_json(sess_path, sess)
    print("finalized: queue on standby, audio cleaned, session closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
