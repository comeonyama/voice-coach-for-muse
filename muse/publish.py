#!/usr/bin/env python3
"""Publish coaching messages to the VCM queue on the Cloudflare Worker.

Upload path: HTTPS POST to https://<VCM_WORKER_HOST>/upload
(op=audio / op=queue / op=cleanup), authenticated with the X-Upload-Token
credential held by the custom.vcm-worker-upload connection (surrogate
exchange; the real token never appears in this script, its arguments, or
its logs). Queue and audio live in R2 behind the Worker; the public read
side is https://<VCM_WORKER_HOST>/queue.json.

Local mirror of the queue lives in $VCM_QUEUE_DIR (default ~/vcm/queue)
(queue.json + audio/). Commands:
  init <session>            start a fresh local queue (empty messages)
  say <id> <text>           TTS (Aria, ja) -> upload audio -> append -> upload queue
  post <id> <text>          append a text-only message (audio: null) -> upload queue
  sync                      upload the local queue.json as-is
  cleanup [min-age-sec]     delete unreferenced MP3s older than the grace period
  history <file>            upload a history JSON ({"runs": [...]}) via op=history
"""
import json, subprocess, sys, datetime, os
import urllib.request
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

BASE = os.environ.get("VCM_QUEUE_DIR", os.path.expanduser("~/vcm/queue"))
HOST = os.environ.get("VCM_WORKER_HOST", "vcm-queue.example.workers.dev")
ENDPOINT = f"https://{HOST}/upload"
JST = datetime.timezone(datetime.timedelta(hours=9))
# Muse TTS voice for coaching messages (Aria). Override with VCM_TTS_VOICE.
VOICE = os.environ.get("VCM_TTS_VOICE", "avocado_v2:MAI_01")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"

def post(query, body, content_type):
    req = urllib.request.Request(f"{ENDPOINT}?{query}", data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", UA)
    add_surrogate_to_request(req, os.environ.get("VCM_CONNECTOR_ID", "custom.vcm-worker-upload"), allowed_hosts=[HOST])
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, read_json_response(e)
        except Exception:
            return e.code, {"ok": False, "error": "non-JSON error response"}

def load_queue():
    with open(f"{BASE}/queue.json", encoding="utf-8") as f:
        return json.load(f)

def save_queue(q):
    with open(f"{BASE}/queue.json", "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
        f.write("\n")

def upload_queue(q):
    body = (json.dumps(q, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return post("op=queue", body, "application/json")

def cmd_init(session):
    os.makedirs(f"{BASE}/audio", exist_ok=True)
    q = {"session": session, "messages": []}
    save_queue(q)
    print(json.dumps({"ok": True, "session": session}))

def _normalize(raw, mp3):
    """Raise loudness to a consistent, outdoor-audible level.

    Same mastering as the XServer-era publisher: loudnorm to -11 LUFS
    with a -1.0 dB true-peak ceiling (about +9.5 dB over the raw TTS
    output), confirmed with the user in the 2026-11-01 field test.
    """
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw,
                    "-af", "loudnorm=I=-11:TP=-1.0:LRA=7,volume=1.4,alimiter=limit=0.84",
                    "-c:a", "libmp3lame", "-ar", "24000", "-ac", "1", mp3],
                   check=True, timeout=60)

def synthesize(text, mp3):
    """Generate speech for one message.

    The TTS engine inserts multi-second silences and occasional garbled
    words on long multi-sentence input, so synthesize sentence by
    sentence in parallel and join with a fixed 0.35 s gap instead.
    """
    import re
    from concurrent.futures import ThreadPoolExecutor
    parts = [p for p in re.findall(r"[^。！？]+[。！？]?", text) if p.strip()]
    raw = mp3 + ".raw.mp3"
    if len(parts) <= 1:
        r = subprocess.run(["/opt/hatch/bin/tts", "speak", "--text", text,
                            "--voice", VOICE, "--language", "ja",
                            "--output", raw], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[:200])
        _normalize(raw, mp3)
        return
    tmp = f"{mp3}.parts"
    os.makedirs(tmp, exist_ok=True)
    def gen(i_part):
        i, part = i_part
        out = f"{tmp}/{i:02d}.mp3"
        r = subprocess.run(["/opt/hatch/bin/tts", "speak", "--text", part,
                            "--voice", VOICE, "--language", "ja",
                            "--output", out], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[:200])
        return out
    with ThreadPoolExecutor(max_workers=4) as ex:
        files = list(ex.map(gen, enumerate(parts)))
    gap = f"{tmp}/gap.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=24000:cl=mono", "-t", "0.35", gap],
                   check=True, timeout=30)
    entries = []
    for j, f in enumerate(files):
        if j:
            entries.append(gap)
        entries.append(f)
    listfile = f"{tmp}/list.txt"
    with open(listfile, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(f"file '{e}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", listfile, "-c:a", "libmp3lame", "-ar", "24000",
                    "-ac", "1", raw], check=True, timeout=60)
    _normalize(raw, mp3)

def _append_and_upload(mid, text, audio):
    q = load_queue()
    now = datetime.datetime.now(JST)
    q["messages"].append({"id": mid,
                          "ts": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                          "audio": audio, "text": text})
    status, resp = upload_queue(q)
    if status != 200 or not resp.get("ok"):
        print(json.dumps({"ok": False, "stage": "queue", "status": status, "resp": resp}))
        return False
    save_queue(q)
    print(json.dumps({"ok": True, "id": mid, "messages": len(q["messages"])}))
    return True

def cmd_say(mid, text):
    os.makedirs(f"{BASE}/audio", exist_ok=True)
    mp3 = f"{BASE}/audio/{mid}.mp3"
    try:
        synthesize(text, mp3)
    except Exception as e:
        print(json.dumps({"ok": False, "stage": "tts", "error": str(e)[:200]}))
        return
    with open(mp3, "rb") as f:
        audio = f.read()
    status, resp = post(f"op=audio&name={mid}.mp3", audio, "audio/mpeg")
    if status != 200 or not resp.get("ok"):
        print(json.dumps({"ok": False, "stage": "audio", "status": status, "resp": resp}))
        return
    _append_and_upload(mid, text, f"audio/{mid}.mp3")

def cmd_post(mid, text):
    _append_and_upload(mid, text, None)

def cmd_sync():
    q = load_queue()
    status, resp = upload_queue(q)
    print(json.dumps({"ok": status == 200 and resp.get("ok"), "status": status, "resp": resp}))

def cmd_history(path):
    with open(path, encoding="utf-8") as f:
        hist = json.load(f)
    if not isinstance(hist, dict) or not isinstance(hist.get("runs"), list):
        print(json.dumps({"ok": False, "stage": "validate", "error": "history must be an object with a runs array"}))
        return
    body = (json.dumps(hist, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    status, resp = post("op=history", body, "application/json")
    ok = status == 200 and resp.get("ok")
    if ok:
        with open(f"{BASE}/history.json", "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps({"ok": ok, "status": status, "resp": resp}))

def cmd_history_append():
    """Append the current local queue as a run in history.json and upload.

    Called by the monitor at finish time, while the local queue mirror
    still holds the just-finished run (before the standby init wipes it).
    Without this, a run's messages vanish from the app's history as soon
    as the queue is re-initialised.
    """
    q = load_queue()
    session = q.get("session") or ""
    msgs = [{"id": m["id"], "ts": m["ts"], "text": m["text"]}
            for m in q.get("messages", [])]
    if not session or session == "standby" or not msgs:
        print(json.dumps({"ok": True, "skipped": True}))
        return
    hpath = f"{BASE}/history.json"
    hist = {"runs": []}
    if os.path.exists(hpath):
        with open(hpath, encoding="utf-8") as f:
            hist = json.load(f)
    runs = [r for r in hist.get("runs", []) if r.get("session") != session]
    date = session[4:14] if session.startswith("run-") and len(session) >= 14 else msgs[0]["ts"][:10]
    runs.insert(0, {"session": session, "date": date, "messages": msgs})
    hist["runs"] = runs[:10]
    body = (json.dumps(hist, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    status, resp = post("op=history", body, "application/json")
    ok = status == 200 and resp.get("ok")
    if ok:
        with open(hpath, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps({"ok": ok, "status": status, "session": session, "messages": len(msgs)}))

def cmd_cleanup(min_age_sec):
    body = json.dumps({"min_age_sec": min_age_sec}).encode("utf-8")
    status, resp = post("op=cleanup", body, "application/json")
    print(json.dumps({"ok": status == 200 and resp.get("ok"), "status": status, "resp": resp}))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "init":
        cmd_init(sys.argv[2])
    elif cmd == "say":
        cmd_say(sys.argv[2], sys.argv[3])
    elif cmd == "post":
        cmd_post(sys.argv[2], sys.argv[3])
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "cleanup":
        cmd_cleanup(int(sys.argv[2]) if len(sys.argv) > 2 else 3600)
    elif cmd == "history":
        cmd_history(sys.argv[2])
    elif cmd == "history-append":
        cmd_history_append()
    else:
        sys.exit(f"unknown command: {cmd}")
