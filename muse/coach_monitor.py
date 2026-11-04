#!/usr/bin/env python3
"""Voice Coach for Muse - generalized coaching monitor.

Architecture: the program drives, the agent supplies content.
This monitor owns everything that must behave deterministically:

  - the polling loop and its interval,
  - the message spine (distance read in Japanese, average pace,
    heart-rate judgment from configured bands),
  - when to speak (per-km or fixed interval, plus event triggers:
    sustained too-fast pace, hydration reminders, milestones),
  - the no-repeat rule for comments (persisted in a file that survives
    restarts and content switches),
  - end-of-run handling (an ended session is a finish, not a failure),
    including the closing message, a wait, and the standby/cleanup
    sequence for the delivery queue.

The agent (e.g. Muse) supplies, per run:
  - session.json: {"session_id": ..., "token": ...} extracted from the
    LiveTrack start notification mail,
  - a "neta" file: today's prepared comments as a JSON list of strings,
    built before the run from weather, calendar, mail and news
    (see the comment template collection),
  - configuration (coach_config.json).

Delivery mode (config "audio", default true):
  - true: messages go through the publisher's "say" command and carry
    an MP3 synthesized on the agent side. If "say" fails after all
    retries, the same message falls back to a text-only "post"
    (config "tts_fallback", default true) so the app can speak it
    with on-device TTS instead of losing it (decided 2026-11-04).
  - false: messages go through the publisher's "post" command as text
    only (audio: null); the receiver app speaks them with on-device
    TTS. Match this with the app's playback-mode setting for the run.

Comment selection order per message:
  1. important advice (too-fast pace with cooldown, hydration reminder)
  2. milestone (configured km marks)
  3. progress note (every Nth message, if enabled)
  4. motivational line (every Nth message, if enabled)
  5. neta pool (in order), then tips pool (in order)
  6. generated progress fallback
Every delivered comment is appended to used_comments.json in the state
directory and is never repeated (important advice is exempt).

Usage:
  coach_monitor.py --config coach_config.json --session session.json \
      [--neta neta.json] [--stock comment_stock.json]

Stop conditions: session end detected, stale points (config), stop flag
file (state_dir/coach_stop), max duration, or max messages.
Exit code 0 = finished normally, 2 = aborted (poll failures).
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import time
from collections import deque
from configuration import validate_config
from delivery import publisher_call
from runtime import private_dir, private_append, write_json, error_info, read_state, valid_session, file_lock

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
    """4.4 -> 'yon ten yon' (Japanese reading; TTS reads decimals in English otherwise)."""
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


class Monitor:
    def __init__(self, cfg, session, neta, stock):
        validate_config(cfg)
        self.cfg = cfg
        self.session = session
        self.state_dir = pathlib.Path(cfg["state_dir"])
        private_dir(self.state_dir)
        self.tz = datetime.timezone(datetime.timedelta(hours=cfg.get("timezone_offset_hours", 9)))
        self.log_path = self.state_dir / "coach_log.jsonl"
        self.used_path = self.state_dir / "used_comments.json"
        self.used = self._load_used()
        self.neta = list(neta)
        self.tips = list(stock.get("tips", []))
        self.motivational = list(stock.get("motivational", []))
        self.seq = self._initial_seq()
        self.sent = 0
        self.fail_streak = 0
        self.hist = deque()
        self.last_send = 0.0
        self.last_send_km = 0
        self.last_fast_advice = 0.0
        self.hydration_marks_done = set()
        self.milestones_done = set()
        # Last known good values: the closing message is built from these
        # when the session-end response carries no status (observed
        # 2026-11-03, when the closing went out as "0 km / pace -").
        # Seeded from the poller's state file so a mid-run restart of the
        # monitor still closes with real figures.
        self.last_dist = 0.0
        self.last_dur = 0
        try:
            ps = read_state(self.state_dir / "poll_state.json", {},
                            valid=lambda value: value.get("session_id") == session["session_id"])
            lp = ps.get("last_point") or {}
            if lp.get("dist_m"):
                self.last_dist = float(lp["dist_m"]) / 1000.0
                self.last_dur = lp.get("dur_s") or 0
        except Exception:
            pass
        # True once any live value has been observed (or seeded from a
        # previous segment). If the session turns out to have ended before
        # a single live value arrived, finish() sends a dedicated notice
        # instead of a closing built from zeros.
        self.saw_live = self.last_dist >= 0.05
        self.start_announced = False

    # ---------- infrastructure ----------
    def log(self, obj):
        obj["at"] = datetime.datetime.now(self.tz).isoformat(timespec="seconds")
        with private_append(self.log_path) as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _load_used(self):
        try:
            return set(json.loads(self.used_path.read_text()))
        except Exception:
            return set()

    def _save_used(self):
        write_json(self.used_path, sorted(self.used))

    def _initial_seq(self):
        qpath = self.cfg.get("queue_json")
        if qpath:
            try:
                q = json.loads(pathlib.Path(qpath).read_text())
                return len(q.get("messages", []))
            except Exception:
                return 0
        return 0

    def poll(self):
        r = subprocess.run(
            ["python3", self.cfg["poll_script"],
             "--session-id", self.session["session_id"],
             "--token", self.session["token"],
             "--state", str(self.state_dir / "poll_state.json")],
            capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)

    def publish(self, *args):
        result = publisher_call(self.cfg, *args)
        self.last_publish_out = json.dumps(result)
        return result["ok"]

    def publish_retry(self, *args, attempts=3):
        backoff = float(self.cfg.get("say_retry_backoff_sec", 5))
        ok = False
        for i in range(max(1, attempts)):
            ok = self.publish(*args)
            if ok:
                break
            if i + 1 < attempts:
                time.sleep(backoff)
                backoff *= 2
        return ok

    def say(self, text, attempts=None):
        self.seq += 1
        mid = f"run-{self.seq:03d}"
        verb = "say" if self.cfg.get("audio", True) else "post"
        if attempts is None:
            attempts = int(self.cfg.get("say_attempts", 3))
        # Retrying with the same message id is safe: the publisher
        # replaces the whole queue on upload and reuses the same audio
        # filename, so a retry cannot duplicate the message. A single
        # transient failure (TTS or upload) must not silently drop a
        # message (observed 2026-11-04: the closing message was lost
        # to one failed publish with no retry).
        backoff = float(self.cfg.get("say_retry_backoff_sec", 5))
        ok = False
        tried = 0
        for i in range(max(1, attempts)):
            tried = i + 1
            ok = self.publish(verb, mid, text)
            if ok:
                break
            if i + 1 < attempts:
                self.log({"event": "say_retry", "id": mid, "attempt": tried,
                          "out": getattr(self, "last_publish_out", "")})
                time.sleep(backoff)
                backoff *= 2
        fallback = False
        if not ok and verb == "say" and self.cfg.get("tts_fallback", True):
            # The MP3 path failed end to end (TTS, audio upload, or
            # queue). Deliver the same text with audio: null instead,
            # so the app speaks it with on-device TTS and the message
            # is not lost (decided 2026-11-04).
            fallback = True
            ok = self.publish_retry("post", mid, text,
                                    attempts=max(1, attempts))
        self.log({"event": "say", "id": mid, "ok": ok, "mode": verb,
                  "text": text, "attempts": tried, "fallback": fallback})
        return ok

    # ---------- message composition ----------
    def hr_judgment(self, hr):
        if hr is None:
            return None
        for band in self.cfg.get("hr_bands", []):
            if band.get("max") is None or hr <= band["max"]:
                return band["label"]
        return None

    def spine(self, dist, avg, hr, past=False):
        s = f"{dist_spoken(dist)}キロ。平均ペースはキロ{pace_str(avg)}"
        s += "でした。" if past else "です。"
        hj = self.hr_judgment(hr)
        if hj:
            s += f"心拍は{hj}です。"
        return s

    def pick_comment(self, dist, dur, session_start_iso, recent_pace, now):
        cfg = self.cfg
        # 1) important advice: sustained too-fast pace (repeatable, cooldown)
        fast_limit = (cfg.get("target_pace") or {}).get("fast_limit_sec")
        cooldown = cfg.get("fast_advice_cooldown_sec", 300)
        if fast_limit and recent_pace and recent_pace < fast_limit \
                and now - self.last_fast_advice > cooldown:
            self.last_fast_advice = now
            return (f"直近はキロ{pace_str(recent_pace)}と上がり気味です。"
                    "今日の目的に合わせて、少し落としておきましょう。"), True
        # 1b) hydration reminder at configured duration multiples
        hyd = cfg.get("hydration") or {}
        if hyd.get("enabled"):
            every = hyd.get("every_min", 40) * 60
            if dur >= hyd.get("min_run_min", 75) * 60:
                mark = int(dur // every)
                if mark >= 1 and mark not in self.hydration_marks_done:
                    self.hydration_marks_done.add(mark)
                    return "そろそろ給水のタイミングです。喉が渇く前に、一口どうぞ。", False
        # 2) milestones
        for km in cfg.get("milestones_km", [5, 10, 15, 20, 25, 30, 35, 40]):
            if dist >= km and km not in self.milestones_done:
                self.milestones_done.add(km)
                return f"{dist_spoken(float(km))}キロを超えました。この調子でどうぞ。", False
        upcoming = self.sent + 1
        # 3) progress note
        pe = cfg.get("progress_every", 0)
        if pe and upcoming % pe == 0 and session_start_iso:
            emin = self._elapsed_min(session_start_iso)
            if emin:
                return f"スタートから{emin}分が過ぎました。いいリズムで来ています。", False
        # 4) motivational line, sparsely
        me = cfg.get("motivational_every", 0)
        if me and upcoming % me == 0:
            c = self._take(self.motivational)
            if c:
                return c, False
        # 5) neta, then tips
        c = self._take(self.neta) or self._take(self.tips)
        if c:
            return c, False
        # 6) generated fallback (always fresh: numbers differ)
        emin = self._elapsed_min(session_start_iso) or 0
        return f"スタートから{emin}分が過ぎました。残りもこの調子でどうぞ。", False

    def _take(self, pool):
        while pool:
            cand = pool.pop(0)
            if cand not in self.used:
                return cand
        return None

    def _elapsed_min(self, start_iso):
        if not start_iso:
            return None
        try:
            t0 = datetime.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            return int((datetime.datetime.now(datetime.timezone.utc) - t0).total_seconds() // 60)
        except Exception:
            return None

    # ---------- run lifecycle ----------
    def finish(self, dist, avg, reason):
        cfg = self.cfg
        no_live = not self.saw_live and not (dist and dist > 0.05)
        if no_live:
            text = cfg.get("no_live_closing_text",
                           "監視を開始しましたが、アクティビティが終了しました。")
        else:
            closing = cfg.get("closing_text",
                               "{distance}キロ。平均ペースはキロ{pace}でした。おつかれさまでした。")
            text = closing.replace("{distance}", dist_spoken(dist)).replace("{pace}", pace_str(avg))
        ok = self.say(text, attempts=int(cfg.get("closing_attempts", 4)))
        entry = {"event": "closing_sent", "ok": ok, "reason": reason}
        if no_live:
            entry["no_live"] = True
        self.log(entry)
        wait = (cfg.get("close") or {}).get("wait_before_standby_sec", 60)
        if wait:
            time.sleep(wait)
        # Preserve this run in the app's history before the queue is wiped.
        self.publish_retry("history-append")
        standby = (cfg.get("close") or {}).get("standby_session", "standby")
        self.publish_retry("init", standby)
        self.publish_retry("sync")
        self.publish_retry("cleanup", "0")
        # Mark the state-dir session record closed, so the session watcher
        # does not mistake this finished run for a leftover live session
        # when the next run's session arrives.
        try:
            sp = self.state_dir / "session.json"
            if sp.exists():
                sdoc = json.loads(sp.read_text())
                if sdoc.get("state") == "live":
                    sdoc["state"] = "closed"
                    sdoc["closed_by"] = "coach_monitor"
                    write_json(sp, sdoc)
        except Exception:
            pass
        self.log({"event": "finished", "dist": dist, "reason": reason})
        return 0

    def run(self):
        cfg = self.cfg
        self.log({"event": "monitor_start", "seq_from": self.seq})
        start_wall = time.time()
        max_dur = cfg.get("max_duration_min", 360) * 60
        max_msg = cfg.get("max_messages", 200)
        poll_interval = cfg.get("poll_interval_sec", 20)
        mode = cfg.get("send_mode", "per_km")
        send_interval = cfg.get("send_interval_sec", 60)
        stale_sec = cfg.get("stale_finish_sec", 600)
        # ~10 minutes of failed polls before giving up: a brief LiveTrack
        # API outage (about 2 minutes on 2026-11-03) must not end monitoring.
        fail_abort = cfg.get("poll_fail_abort", 30)

        while time.time() - start_wall < max_dur and self.sent < max_msg:
            try:
                if (self.state_dir / "coach_stop").exists():
                    self.log({"event": "stopped_by_flag"})
                    return 0
                res = self.poll()
                session = res.get("session") or {}
                if not res.get("ok"):
                    # An ended session surfaces as a failed poll whose
                    # payload says ended: that is a finish, not a failure.
                    if session.get("ended"):
                        st = res.get("status") or {}
                        dist = st.get("distance_km") or self.last_dist
                        dur = st.get("duration_secs") or self.last_dur
                        avg = (dur / dist) if dist > 0.05 else None
                        return self.finish(dist, avg, "session_ended")
                    self.fail_streak += 1
                    if self.fail_streak >= fail_abort:
                        self.log({"event": "abort", "reason": "poll failing"})
                        return 2
                    time.sleep(poll_interval)
                    continue
                self.fail_streak = 0
                st = res.get("status", {})
                dist = st.get("distance_km") or 0.0
                dur = st.get("duration_secs") or 0
                hr = st.get("current_hr")
                if dist > 0:
                    self.last_dist, self.last_dur = dist, dur
                    if dist >= 0.05:
                        self.saw_live = True
                now = time.time()
                self.hist.append((now, dist))
                while self.hist and self.hist[0][0] < now - 100:
                    self.hist.popleft()
                recent_pace = None
                if len(self.hist) >= 2:
                    dt = self.hist[-1][0] - self.hist[0][0]
                    dd = self.hist[-1][1] - self.hist[0][1]
                    if dt > 20 and dd > 0.005:
                        recent_pace = dt / dd
                avg = (dur / dist) if dist > 0.05 else None
                self.log({"event": "poll", "dist": dist, "dur": dur, "hr": hr,
                          "recent_pace": recent_pace})

                age = st.get("last_point_age_secs") or 0
                if session.get("ended") or age > stale_sec:
                    fdist = dist if dist > 0 else self.last_dist
                    favg = avg if dist > 0 else ((self.last_dur / fdist) if fdist > 0.05 else None)
                    return self.finish(fdist, favg, "ended" if session.get("ended") else "stale")

                if not self.start_announced:
                    self.start_announced = True
                    announce = cfg.get("start_announce_text", "監視を開始しました。")
                    if announce:
                        self.say(announce)

                due = False
                if mode == "interval":
                    due = (now - self.last_send >= send_interval)
                else:  # per_km: first message early, then each new km
                    km = int(dist)
                    due = (self.sent == 0 and dist >= 0.3) or km > self.last_send_km
                if due:
                    comment, is_advice = self.pick_comment(dist, dur, session.get("start"),
                                                           recent_pace, now)
                    if self.say(self.spine(dist, avg, hr) + comment):
                        self.sent += 1
                        self.last_send_km = int(dist)
                        if not is_advice:
                            self.used.add(comment)
                            self._save_used()
                    self.last_send = now
                time.sleep(poll_interval)
            except Exception as e:
                self.log({"event": "loop_error", **error_info(e, "monitor_loop")})
                time.sleep(poll_interval)
        self.log({"event": "timeout_or_cap"})
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--session", required=True, help='JSON file: {"session_id", "token"}')
    ap.add_argument("--neta", help="JSON list of prepared comments for this run")
    ap.add_argument("--stock", help='JSON file: {"tips": [...], "motivational": [...]}')
    args = ap.parse_args()

    cfg = json.loads(pathlib.Path(args.config).read_text())
    session = read_state(args.session, valid=valid_session)
    if session is None:
        ap.error("session file is missing or invalid; invalid files are quarantined")
    neta = json.loads(pathlib.Path(args.neta).read_text()) if args.neta else []
    stock = {"tips": [], "motivational": []}
    stock_path = args.stock or cfg.get("stock_file")
    if stock_path:
        stock.update(json.loads(pathlib.Path(stock_path).read_text()))
    try:
        mon = Monitor(cfg, session, neta, stock)
    except ValueError as e:
        ap.error(str(e))
    try:
        with file_lock(mon.state_dir / ".monitor.lock", blocking=False):
            raise SystemExit(mon.run())
    except BlockingIOError:
        ap.error("another monitor owns this state directory")


if __name__ == "__main__":
    main()
