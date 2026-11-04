#!/usr/bin/env python3
"""VCM wait announcer: while Muse waits for the Garmin LiveTrack start
mail, keep the runner informed by voice every minute.

Why: after the user asks Muse to monitor a run, there is a silent gap
until the start mail is detected (mail arrival + watcher interval). In
the 2026-11-03 field test the user asked for a periodic voice notice so
the waiting state itself is audible through the receiver app.

Behaviour:
  - Initialises the delivery queue with the run's session name first
    (this also satisfies the rule "publish init before the run starts";
    starting from a leftover session name caused app-side trouble).
  - Posts a short waiting message (publisher "say", rotating texts,
    ids wait-001, wait-002, ...) every --interval seconds (default 60).
  - Stops as soon as one of these holds:
      * session.json appears in the state dir (mail detected),
      * a run-* message appears in the queue mirror (monitor took over),
      * the coach_stop flag file appears,
      * --max-min minutes elapsed (default 30).

Usage:
  wait_announce.py --config coach_config.json --session run-2026-11-09
Typically launched in the background by run_watch.py; its stdout is
meant to be redirected to a log file in the state directory.
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys
import time

TEXTS = [
    "監視を開始しました。LiveTrack の開始メールを待っています。届き次第、コーチングを始めます。",
    "待機中です。開始メールはまだ届いていません。",
    "引き続き待機中です。Garmin の LiveTrack を開始すると、コーチングが始まります。",
]


def now():
    tz = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.now(tz).isoformat(timespec="seconds")


class Announcer:
    def __init__(self, cfg, session_name, interval, max_min):
        self.pub = cfg["publish_script"]
        self.state_dir = pathlib.Path(cfg["state_dir"])
        self.queue_json = cfg.get("queue_json")
        self.session_name = session_name
        self.interval = interval
        self.deadline = time.time() + max_min * 60
        self.seq = 0

    def publish(self, *args):
        r = subprocess.run(["python3", self.pub, *args],
                           capture_output=True, text=True, timeout=120)
        return '"ok": true' in (r.stdout or "")

    def stop_reason(self):
        if (self.state_dir / "session.json").exists():
            return "session_detected"
        if (self.state_dir / "coach_stop").exists():
            return "stop_flag"
        if self.queue_json:
            try:
                q = json.loads(pathlib.Path(self.queue_json).read_text())
                for m in q.get("messages", []):
                    if str(m.get("id", "")).startswith("run-"):
                        return "monitor_took_over"
            except Exception:
                pass
        if time.time() > self.deadline:
            return "max_wait"
        return None

    def run(self):
        ok = self.publish("init", self.session_name)
        self.publish("sync")
        print(f"{now()} wait_announce start session={self.session_name} init_ok={ok}", flush=True)
        while True:
            reason = self.stop_reason()
            if reason:
                print(f"{now()} wait_announce stop reason={reason} sent={self.seq}", flush=True)
                return 0
            self.seq += 1
            text = TEXTS[(self.seq - 1) % len(TEXTS)]
            ok = self.publish("say", f"wait-{self.seq:03d}", text)
            print(f"{now()} wait_announce say wait-{self.seq:03d} ok={ok}", flush=True)
            # Sleep in short slices so a detection stops us promptly.
            slept = 0.0
            while slept < self.interval:
                if self.stop_reason():
                    break
                time.sleep(5)
                slept += 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="coach config JSON (publish_script, state_dir, queue_json)")
    ap.add_argument("--session", required=True, help="queue session name for this run")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--max-min", type=int, default=30)
    args = ap.parse_args()
    cfg = json.loads(pathlib.Path(args.config).read_text())
    ann = Announcer(cfg, args.session, args.interval, args.max_min)
    raise SystemExit(ann.run())


if __name__ == "__main__":
    sys.exit(main())
