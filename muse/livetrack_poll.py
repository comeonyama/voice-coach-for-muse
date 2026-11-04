#!/usr/bin/env python3
"""Garmin LiveTrack one-shot poller (Voice Coach for Muse).

Fetches new track points for a LiveTrack session from the livetrack.garmin.com
web API (the same endpoints the public viewer page uses), maintains
incremental state (cursor, per-km splits), and prints a single JSON object
describing what is new since the previous call. The coaching agent calls this
repeatedly (every 20-30 seconds) while monitoring a run.

How to get the session id and token:
  When a LiveTrack session starts, Garmin emails the share link to the
  recipients configured in Garmin Connect. The link looks like:

      https://livetrack.garmin.com/session/<SESSION_ID>/token/<TOKEN>

  Extract both values from that URL (the coaching agent reads the mail via
  its Gmail connection). Treat the token as a credential: anyone with the
  link can see the runner's live location. Keep it in state files only.

Usage:
  livetrack_poll.py --session-id <uuid> --token <hex> --state <state.json>

State file is created if missing. Point --state at a per-run file and keep
using the same file for the whole run; use a fresh file for the next run.

Output JSON fields:
  ok: bool
  error: str (when ok is false)
  session: {start, end, ended}
  status: {distance_km, duration_secs, last_point_age_secs,
           current_hr, current_pace_secs_per_km, current_pace, points_total}
  new_splits: [{km, split_secs, pace, avg_hr}]

Notes on the (unofficial) API behaviour, verified 2026-10-29 / 2026-11-01:
  - The API needs the viewer page's cookies plus a `Livetrack-Csrf-Token`
    header taken from the page's <meta name="csrf-token"> tag; see
    bootstrap(). Without it the API returns a bare 403.
  - For an ended session the track-points endpoint returns 403 with the
    structured error "1008 SESSION_ACCESS_DENIED", while the session info
    still reports `ended: true`. Treat that combination as "run finished",
    not as a failure.
"""
import argparse
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from runtime import private_dir, error_info

BASE = "https://livetrack.garmin.com"
UA = {"User-Agent": "Mozilla/5.0 (Voice Coach for Muse livetrack poller)", "Accept": "application/json"}


def bootstrap(session_id, token):
    """Load the viewer page once; return (opener, csrf_token).

    The livetrack API requires the page's cookies plus a
    `Livetrack-Csrf-Token` header whose value is in the page's
    <meta name="csrf-token"> tag. Without this the API returns a bare 403;
    with it, an ended session returns the structured error
    1008 SESSION_ACCESS_DENIED.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = ""
    try:
        req = urllib.request.Request(
            f"{BASE}/session/{session_id}/token/{token}",
            headers={"User-Agent": UA["User-Agent"]},
        )
        with opener.open(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if m:
            csrf = m.group(1)
    except Exception:
        pass
    return opener, csrf


def http_json(url, opener=None, csrf=""):
    headers = dict(UA)
    if csrf:
        headers["Livetrack-Csrf-Token"] = csrf
    req = urllib.request.Request(url, headers=headers)
    try:
        if opener is not None:
            resp = opener.open(req, timeout=20)
        else:
            resp = urllib.request.urlopen(req, timeout=20)
        with resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body) if body.strip() else None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, None
    except Exception as e:  # network error etc.
        return None, {"_error": type(e).__name__}


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def getf(point, key):
    """Read a fitness field from a track point, tolerating nesting."""
    if key in point and point[key] is not None:
        return point[key]
    fp = point.get("fitnessPointData") or {}
    return fp.get(key)


def fmt_pace(secs):
    if not secs or secs <= 0:
        return None
    secs = int(round(secs))
    return f"{secs // 60}:{secs % 60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--state", required=True)
    args = ap.parse_args()

    state = {
        "session_id": args.session_id,
        "next_begin": None,       # ISO datetime cursor for incremental fetch
        "last_point": None,       # {dist_m, dur_s, ts} of previous point
        "km_done": 0,             # highest km split already computed
        "km_hr_sum": 0.0,
        "km_hr_n": 0,
        "km_start_dur": 0.0,      # duration at last km crossing
        "points_total": 0,
        "last_point_ts": None,
    }
    if os.path.exists(args.state):
        try:
            with open(args.state, encoding="utf-8") as fh:
                saved = json.load(fh)
            if saved.get("session_id") == args.session_id:
                state.update(saved)
        except Exception:
            pass

    out = {"ok": False}

    opener, csrf = bootstrap(args.session_id, args.token)

    # --- session info (best effort; shapes vary) ---
    s_status, s_json = http_json(f"{BASE}/api/v2/sessions/{args.session_id}?token={args.token}", opener, csrf)
    session_info = {"start": None, "end": None, "ended": False}
    if s_status == 200 and isinstance(s_json, dict):
        node = s_json.get("session") if isinstance(s_json.get("session"), dict) else s_json
        start = node.get("start") or node.get("startTime") or node.get("startDate")
        end = node.get("end") or node.get("endTime") or node.get("endDate")
        session_info["start"] = start
        session_info["end"] = end
        st = parse_ts(start)
        en = parse_ts(end)
        if st and en:
            # While live, Garmin sets end ~= start + 24h as a placeholder.
            session_info["ended"] = (en - st).total_seconds() < 23 * 3600 + 59 * 60

    # --- track points (incremental) ---
    url = f"{BASE}/api/sessions/{args.session_id}/track-points/common?token={args.token}"
    if state["next_begin"]:
        url += f"&begin={urllib.request.quote(state['next_begin'], safe='')}"
    p_status, p_json = http_json(url, opener, csrf)
    if p_status == 403:
        detail = ""
        blob = ""
        if isinstance(p_json, dict):
            errs = p_json.get("errors") or []
            blob = json.dumps(errs, ensure_ascii=False)
            if errs and isinstance(errs[0], dict):
                detail = f" {errs[0].get('message', '')}".strip()
        # An ended session denies track-point access with the structured
        # error 1008 (SESSION_ACCESS_DENIED). That denial can arrive
        # before the session info's end timestamp settles (observed
        # 2026-11-04: points were denied minutes before `ended` flipped),
        # so treat the denial itself as the end signal; the monitor
        # finishes on the first denied poll instead of waiting for the
        # timestamp heuristic.
        if "1008" in blob or "SESSION_ACCESS_DENIED" in blob:
            session_info["ended"] = True
        out.update({"ok": False, "error": "http_403" + (f" {detail}" if detail else " invalid_session_or_token"), "session": session_info})
        print(json.dumps(out, ensure_ascii=False))
        return
    if p_status != 200 or not isinstance(p_json, dict):
        err = (p_json or {}).get("_error") if isinstance(p_json, dict) else None
        out.update({"ok": False, "error": f"http_{p_status}" + (f" {err}" if err else ""), "session": session_info})
        print(json.dumps(out, ensure_ascii=False))
        return

    points = p_json.get("trackPoints") or p_json.get("points") or []
    new_splits = []
    now = datetime.now(timezone.utc)

    for pt in points:
        if not isinstance(pt, dict):
            continue
        ts_raw = pt.get("dateTime") or pt.get("timestamp")
        dist = getf(pt, "totalDistanceMeters")
        dur = getf(pt, "totalDurationSecs")
        hr = getf(pt, "heartRateBeatsPerMin")
        state["points_total"] += 1
        if ts_raw:
            state["last_point_ts"] = ts_raw
            nxt = parse_ts(ts_raw)
            if nxt:
                state["next_begin"] = datetime.fromtimestamp(nxt.timestamp() + 0.001, timezone.utc).isoformat().replace("+00:00", "Z")
        if dist is None or dur is None:
            continue
        dist = float(dist)
        dur = float(dur)
        prev = state["last_point"]
        if hr:
            state["km_hr_sum"] += float(hr)
            state["km_hr_n"] += 1
        # km crossings (interpolate duration at the crossing)
        while dist >= (state["km_done"] + 1) * 1000.0:
            target = (state["km_done"] + 1) * 1000.0
            if prev and dist > prev["dist_m"]:
                frac = (target - prev["dist_m"]) / (dist - prev["dist_m"])
                cross_dur = prev["dur_s"] + frac * (dur - prev["dur_s"])
            else:
                cross_dur = dur
            split = cross_dur - state["km_start_dur"]
            avg_hr = (state["km_hr_sum"] / state["km_hr_n"]) if state["km_hr_n"] else None
            state["km_done"] += 1
            new_splits.append({
                "km": state["km_done"],
                "split_secs": round(split, 1),
                "pace": fmt_pace(split),
                "avg_hr": round(avg_hr) if avg_hr else None,
            })
            state["km_start_dur"] = cross_dur
            state["km_hr_sum"] = 0.0
            state["km_hr_n"] = 0
        state["last_point"] = {"dist_m": dist, "dur_s": dur, "ts": ts_raw}

    # --- current status from the last usable point ---
    status = {"points_total": state["points_total"]}
    lp = state["last_point"]
    if lp:
        status["distance_km"] = round(lp["dist_m"] / 1000.0, 3)
        status["duration_secs"] = round(lp["dur_s"])
    if state["last_point_ts"]:
        lt = parse_ts(state["last_point_ts"])
        if lt:
            status["last_point_age_secs"] = round((now - lt).total_seconds())
    if points:
        last = points[-1]
        hr = getf(last, "heartRateBeatsPerMin")
        spd = getf(last, "speedMetersPerSec") or last.get("speed")
        if hr:
            status["current_hr"] = hr
        if spd:
            try:
                spd = float(spd)
                if spd > 0.3:
                    status["current_pace_secs_per_km"] = round(1000.0 / spd)
                    status["current_pace"] = fmt_pace(1000.0 / spd)
            except Exception:
                pass

    # save state atomically
    private_dir(os.path.dirname(os.path.abspath(args.state)))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(args.state)), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, args.state)

    out.update({"ok": True, "session": session_info, "status": status, "new_splits": new_splits})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
