#!/usr/bin/env python3
"""VCM run watcher (generalized from the 2026-11-03 one-off).

Standard start procedure for a monitored run:
  1. Launch wait_announce.py in the background: it initialises the
     delivery queue with this run's session name (publish init) and
     speaks a waiting notice every minute until detection.
  2. Poll Gmail for the Garmin LiveTrack start mail. The message body
     MUST be read with --html: the default Markdown rendering drops
     link URLs, which delayed detection by about 5 minutes on
     2026-11-03. Extract session id / token from the raw HTML (or from
     the body_html_file the CLI saves), resolving gar.mn short links
     with curl when needed.
  3. On detection: remember the mail id (state_dir/last_session_mail.json)
     so a later run never re-consumes it, archive the previous
     poll_state.json, write session.json, then run coach_monitor.py.

Usage:
  run_watch.py --account <gmail-account-id> --config <per-run config> \
      --neta <neta.json> [--queue-session run-YYYY-MM-DD] \
      [--deadline-min 25] [--no-announce]

Exit codes: 0 = monitor finished normally, 2 = monitor aborted,
3 = no session mail found before the deadline.
"""
import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

GMAIL = "/opt/hatch/bin/hatch_gws_cli"
URL_RE = re.compile(r"livetrack\.garmin\.com/session/([0-9a-fA-F-]{36})/token/([0-9a-fA-F]+)")
SHORT_RE = re.compile(r"https?://gar\.mn/[A-Za-z0-9]+")
FILE_RE = re.compile(r"[\w./~-]+\.html")
TZ = datetime.timezone(datetime.timedelta(hours=9))


def sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout or ""


def extract(text):
    m = URL_RE.search(text or "")
    if m:
        return m.group(1), m.group(2)
    for short in SHORT_RE.findall(text or ""):
        final = sh(["curl", "-sIL", "-o", "/dev/null", "-w", "%{url_effective}", short], timeout=30)
        m = URL_RE.search(final)
        if m:
            return m.group(1), m.group(2)
    return None


def read_mail_html(account, mid):
    """Return searchable text: CLI stdout plus any saved HTML file body."""
    out = sh([GMAIL, "gmail", "+read", "--account", account, "--id", mid, "--html"], timeout=90)
    texts = [out]
    for path in FILE_RE.findall(out):
        try:
            texts.append(pathlib.Path(path).expanduser().read_text(errors="ignore"))
        except Exception:
            pass
    return "\n".join(texts)


class Watcher:
    def __init__(self, args):
        self.args = args
        cfg = json.loads(pathlib.Path(args.config).read_text())
        self.state = pathlib.Path(args.state_dir or cfg["state_dir"])
        self.state.mkdir(parents=True, exist_ok=True)
        self.last_mail_path = self.state / "last_session_mail.json"

    def last_mail_id(self):
        try:
            return json.loads(self.last_mail_path.read_text()).get("mail_id")
        except Exception:
            return None

    def find_session(self):
        out = sh([GMAIL, "gmail", "+triage", "--account", self.args.account,
                  "--query", "livetrack newer_than:2h", "--max", "5", "--format", "json"])
        try:
            data = json.loads(out)
        except Exception:
            return None
        msgs = data.get("messages") or data.get("results") or (data if isinstance(data, list) else [])
        skip = self.last_mail_id()
        for m in msgs:
            mid = m.get("id") or m.get("message_id") or ""
            if not mid or mid == skip:
                continue
            found = extract(read_mail_html(self.args.account, mid))
            if found:
                return mid, found[0], found[1]
        return None

    def run(self):
        args = self.args
        announcer = None
        if not args.no_announce:
            logf = open(self.state / "wait_announce.log", "a", encoding="utf-8")
            announcer = subprocess.Popen(
                ["python3", str(pathlib.Path(__file__).with_name("wait_announce.py")),
                 "--config", args.config, "--session", args.queue_session],
                stdout=logf, stderr=subprocess.STDOUT)
            print(f"announcer launched pid={announcer.pid} session={args.queue_session}", flush=True)
        deadline = time.time() + args.deadline_min * 60
        found = None
        while time.time() < deadline:
            found = self.find_session()
            if found:
                break
            time.sleep(40)
        if not found:
            print("NO_SESSION_FOUND", flush=True)
            if announcer:
                announcer.terminate()
            return 3
        mid, sid, token = found
        print(f"session found mail={mid} session={sid}", flush=True)
        self.last_mail_path.write_text(json.dumps({"mail_id": mid}))
        prev = self.state / "poll_state.json"
        if prev.exists():
            prev.rename(self.state / "poll_state_prev.json")
        (self.state / "session.json").write_text(json.dumps({
            "session_id": sid, "token": token, "mail_id": mid, "state": "live",
            "started_mail_at": datetime.datetime.now(TZ).isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=1))
        cmd = ["python3", args.monitor, "--config", args.config,
               "--session", str(self.state / "session.json")]
        if args.neta:
            cmd += ["--neta", args.neta]
        print("launching monitor:", " ".join(cmd), flush=True)
        r = subprocess.run(cmd)
        print("monitor exited", r.returncode, flush=True)
        return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="Gmail account id for hatch_gws_cli")
    ap.add_argument("--config", required=True, help="per-run coach config JSON")
    ap.add_argument("--neta", help="prepared comments JSON for this run")
    ap.add_argument("--state-dir", help="defaults to config state_dir")
    ap.add_argument("--queue-session", default="run-" + datetime.datetime.now(TZ).strftime("%Y-%m-%d-%H%M%S"))
    ap.add_argument("--deadline-min", type=int, default=25)
    ap.add_argument("--monitor", default=str(pathlib.Path(__file__).resolve().parent / "coach_monitor.py"))
    ap.add_argument("--no-announce", action="store_true")
    args = ap.parse_args()
    raise SystemExit(Watcher(args).run())


if __name__ == "__main__":
    sys.exit(main())
