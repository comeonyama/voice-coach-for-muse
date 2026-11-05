#!/usr/bin/env python3
"""VCM session watcher: start coaching automatically when GAS posts a session.

Chain: Garmin start mail -> Gmail -> GAS (kicked by the iOS app) ->
Worker R2 "session.json" -> THIS watcher -> coach_monitor.py.

The watcher polls the Worker's authenticated GET /session.json. When a
fresh session appears (received within FRESH_MIN minutes) that it has not
launched yet, it prepares the run exactly like run_watch.py does after
detection (archive poll state, write session.json, init the queue) and
launches coach_monitor.py with the production config. One monitor at a
time. Muse (the chat agent) is not in this loop.

Usage:
  session_watcher.py [--config coach_config.json] [--interval 20]
                     [--once] [--dry-run]
"""
from delivery import publisher_call
import argparse, datetime, json, os, pathlib, subprocess, sys, time
from runtime import private_dir, private_append, write_json, error_info, read_state, valid_session, file_lock, lock_busy
import urllib.request, urllib.error

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

HOST = os.environ.get("VCM_WORKER_HOST", "vcm-queue.example.workers.dev")
SESSION_URL = f"https://{HOST}/session.json"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
BASE = pathlib.Path(os.environ.get("VCM_BASE_DIR", os.path.expanduser("~/vcm")))
WATCHER_STATE = BASE / "watcher_state.json"
JST = datetime.timezone(datetime.timedelta(hours=9))
FRESH_MIN = 20


def log(msg):
    now = datetime.datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    print(f"[{now}] {msg}", flush=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def fetch_session():
    req = urllib.request.Request(SESSION_URL, method="GET")
    req.add_header("User-Agent", UA)
    add_surrogate_to_request(req, os.environ.get("VCM_CONNECTOR_ID", "custom.vcm-worker-upload"), allowed_hosts=[HOST])
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, read_json_response(e)
        except Exception:
            return e.code, {"ok": False, "error": "non-JSON error response"}


def post_start_status(start_id, state):
    if not start_id:
        return True
    payload = dict(start_id=start_id, state=state,
                   at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    req = urllib.request.Request(f"https://{HOST}/upload?op=start-status",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    req.add_header("User-Agent", UA)
    try:
        add_surrogate_to_request(req, os.environ.get("VCM_CONNECTOR_ID", "custom.vcm-worker-upload"), allowed_hosts=[HOST])
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            return response.status == 200 and read_json_response(response).get("ok") is True
    except Exception as error:
        log(json.dumps(error_info(error, "start_status")))
        return False


def load_watcher_state():
    return read_state(WATCHER_STATE, {})


def save_watcher_state(st):
    write_json(WATCHER_STATE, st)


def parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


class Watcher:
    def __init__(self, args):
        self.args = args
        self.cfg = json.loads(pathlib.Path(args.config).read_text())
        self.state_dir = pathlib.Path(self.cfg["state_dir"])
        self.monitor_proc = None
        private_dir(self.state_dir)

    def session_path(self):
        return self.state_dir / "session.json"

    def leftover_live(self, fetched_id):
        cur = read_state(self.session_path(), {}, valid=valid_session)
        return cur.get("state") == "live" and cur.get("session_id") != fetched_id

    def try_finalize(self):
        fin = pathlib.Path(__file__).resolve().parent / "finalize_run.py"
        if not fin.exists():
            fin = BASE / "finalize_run.py"
        if not fin.exists():
            return
        log("leftover live session found; running finalize_run.py")
        r = subprocess.run(["python3", str(fin), "--config", self.args.config],
                           capture_output=True, text=True)
        log(f"finalize_run rc={r.returncode}")

    def launch(self, sess, dry_run, resume=False, prepare_only=False):
        sid = sess["session_id"]
        received = parse_ts(sess.get("received_at", ""))
        qdate = (received.astimezone(JST) if received else datetime.datetime.now(JST))
        # Unique per run: the app treats (session, message id) as played,
        # and message ids restart at run-001 on every launch, so reusing
        # a date-only name makes later same-day runs invisible to the app.
        qsession = "run-" + qdate.strftime("%Y-%m-%d-%H%M%S")
        if dry_run:
            log(f"DRY-RUN would launch monitor for session={sid} queue={qsession}")
            return True
        if not resume:
            prev = self.state_dir / "poll_state.json"
            if prev.exists():
                prev.rename(self.state_dir / "poll_state_prev.json")
            for cmd in (["init", qsession], ["sync"]):
                result = publisher_call(self.cfg, *cmd)
                if not result["ok"]:
                    log(f"queue initialization failed stage={cmd[0]} rc={result.get('returncode')}")
                    return False
            write_json(self.state_dir / "last_session_mail.json", {"mail_id": sess.get("mail_id", "")})
            write_json(self.session_path(), {
                "session_id": sid, "token": sess["token"],
                **({"start_id": sess["start_id"]} if sess.get("start_id") else {}),
                "mail_id": sess.get("mail_id", ""), "state": "live",
                "started_mail_at": sess.get("received_at", ""), "source": "session_watcher",
                "queue_session": qsession,
            })
        st = load_watcher_state()
        retries = st.get("retries", 0) if resume else 0
        st.update(launched_session_id=sid, status="starting", retries=retries,
                  launched_at=datetime.datetime.now(JST).isoformat(timespec="seconds"))
        save_watcher_state(st)
        if prepare_only:
            st.update(status="prepared", start_id=sess.get("start_id"),
                      pending_start_status="monitoring" if sess.get("start_id") else None)
            save_watcher_state(st)
            self.flush_start_status()
            return True
        logf = private_append(self.state_dir / "monitor_auto.log")
        self.monitor_proc = subprocess.Popen(
            ["python3", self.args.monitor, "--config", self.args.config,
             "--session", str(self.session_path())],
            stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        log(f"monitor launched pid={self.monitor_proc.pid} session={sid} queue={qsession}")
        logf.close()
        st["start_id"] = sess.get("start_id")
        st["pending_start_status"] = "monitoring" if sess.get("start_id") else None
        st["status"] = "running"
        save_watcher_state(st)
        self.flush_start_status()
        return True

    def flush_start_status(self):
        st = load_watcher_state()
        if st.get("pending_start_status") and post_start_status(st.get("start_id"), st["pending_start_status"]):
            st.pop("pending_start_status", None)
            save_watcher_state(st)

    def cycle_once(self):
        self.flush_start_status()
        local = read_state(self.session_path(), {}, valid=valid_session)
        st = load_watcher_state()
        if local.get("state") == "closed" and st.get("status") != "complete":
            st.update(status="complete", start_id=local.get("start_id"),
                      pending_start_status="closed" if local.get("start_id") else None)
            save_watcher_state(st)
            self.flush_start_status()
        status, sess = fetch_session()
        if status != 200 or not isinstance(sess, dict) or not valid_session(sess):
            return
        if local.get("session_id") == sess["session_id"]:
            return
        # The monitor's next single cycle finishes the current run first.
        if local.get("state") == "live":
            return
        received = parse_ts(sess.get("received_at", ""))
        if received is None or (datetime.datetime.now(datetime.timezone.utc)-received).total_seconds() > FRESH_MIN*60:
            return
        self.launch(sess, self.args.dry_run, prepare_only=True)

    def cycle(self):
        self.flush_start_status()
        if self.monitor_proc is not None:
            rc = self.monitor_proc.poll()
            if rc is None:
                return  # a run is in progress; do not start another
            log(f"monitor exited rc={rc}")
            self.monitor_proc = None
            st = load_watcher_state()
            st["pending_start_status"] = "closed" if st.get("start_id") else None
            st["status"] = "complete" if rc == 0 else "failed"
            save_watcher_state(st)
            self.flush_start_status()
        if lock_busy(self.state_dir / ".monitor.lock"):
            return
        status, sess = fetch_session()
        if status == 404:
            log("no session posted")
            return
        if status != 200 or not isinstance(sess, dict) or "session_id" not in sess:
            log(f"session fetch unexpected: status={status}")
            return
        sid = sess["session_id"]
        if not valid_session(sess):
            log("session shape invalid")
            return
        st = load_watcher_state()
        if st.get("launched_session_id") == sid:
            local = read_state(self.session_path(), {}, valid=valid_session)
            if st.get("status") == "complete" or local.get("state") == "closed":
                return
            retries = st.get("retries", 0)
            if not isinstance(retries, int) or retries >= 3:
                log("monitor restart limit reached")
                return
            if self.args.dry_run:
                log("DRY-RUN would retry monitor")
                return
            st["retries"] = retries + 1
            save_watcher_state(st)
            if not local:
                write_json(self.session_path(), dict(sess, state="live"))
            self.launch(sess, False, resume=True)
            return
        received = parse_ts(sess.get("received_at", ""))
        if received is None:
            log("session has no received_at; ignoring")
            return
        age_min = (datetime.datetime.now(datetime.timezone.utc) - received).total_seconds() / 60
        if age_min > FRESH_MIN:
            log(f"session {sid} is stale ({age_min:.0f} min old); ignoring")
            return
        if self.leftover_live(sid):
            if self.args.dry_run:
                log("DRY-RUN would finalize previous session")
                return
            self.try_finalize()
            if self.leftover_live(sid):
                log("previous session still live after finalize attempt; waiting")
                return
        self.launch(sess, self.args.dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(BASE / "coach_config.json"))
    ap.add_argument("--monitor", default=str(pathlib.Path(__file__).resolve().parent / "coach_monitor.py"))
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    w = Watcher(args)
    log(f"watcher start (interval={args.interval}s once={args.once} dry_run={args.dry_run})")
    try:
        with file_lock(w.state_dir / ".watcher.lock", blocking=False):
            if args.once:
                with file_lock(w.state_dir / ".monitor.lock", blocking=False):
                    w.cycle_once()
            else:
                watch(w, args)
    except BlockingIOError:
        if not args.once: raise


def watch(w, args):
    while True:
        try:
            w.cycle()
        except Exception as e:
            log(json.dumps(error_info(e, "watcher_cycle")))
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
