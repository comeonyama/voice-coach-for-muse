#!/usr/bin/env python3
"""Publish coaching messages to the Voice Coach for Muse queue.

Upload path: HTTPS POST to <site>/livetrack/upload.php
(op=audio / op=queue / op=cleanup), authenticated with an X-Upload-Token
header. The server side stores only the SHA-256 hash of the token.

Environment variables:
  LIVETRACK_UPLOAD_ENDPOINT  upload.php URL
                             (default: https://discoverroutes.jp/livetrack/upload.php)
  LIVETRACK_UPLOAD_TOKEN     the upload token (required; never hard-code it)
  LIVETRACK_QUEUE_DIR        local mirror of the queue (queue.json + audio/)
                             (default: ./queue next to this script)
  LIVETRACK_TTS_BIN          TTS command used by tts_speak()
                             (default: tts; see tts_speak for the interface)

Commands:
  init <session>            start a fresh local queue (empty messages)
  say <id> <text>           TTS -> upload audio -> append -> upload queue
  sync                      upload the local queue.json as-is
  cleanup [min_age_sec]     delete server audio files not referenced by the
                            current queue and older than min_age_sec
                            (default 3600)
"""
import json, subprocess, sys, datetime, os
import urllib.request, urllib.error

BASE = os.environ.get(
    "LIVETRACK_QUEUE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue"))
ENDPOINT = os.environ.get(
    "LIVETRACK_UPLOAD_ENDPOINT",
    "https://discoverroutes.jp/livetrack/upload.php")
HOST = urllib.request.urlparse(ENDPOINT).hostname
TOKEN = os.environ.get("LIVETRACK_UPLOAD_TOKEN", "")
JST = datetime.timezone(datetime.timedelta(hours=9))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")

# The author's host (XServer) rejects the default Python User-Agent at its
# WAF with 403; use an app-like UA.
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"

def read_json(stream):
    try:
        return json.loads(stream.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "error": "non-JSON response"}

def post(query, body, content_type):
    if not TOKEN:
        raise RuntimeError("LIVETRACK_UPLOAD_TOKEN is not set")
    req = urllib.request.Request(f"{ENDPOINT}?{query}", data=body, method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", UA)
    req.add_header("X-Upload-Token", TOKEN)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, read_json(resp)
    except urllib.error.HTTPError as e:
        return e.code, read_json(e)

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

def tts_speak(text, out):
    """Synthesize one sentence to an MP3 at `out`.

    Interface expected from the TTS command:
      <bin> speak --text <text> --voice <voice> --language ja --output <out>
    In the author's environment this is the Muse TTS CLI (voice
    "avocado_v2:MAI_01"). Replace this function (or point
    LIVETRACK_TTS_BIN at a compatible command) to use another engine;
    24 kHz mono MP3 output is what the concat step below assumes.
    """
    bin_path = os.environ.get("LIVETRACK_TTS_BIN", "tts")
    voice = os.environ.get("LIVETRACK_TTS_VOICE", "avocado_v2:MAI_01")
    r = subprocess.run([bin_path, "speak", "--text", text,
                        "--voice", voice, "--language", "ja",
                        "--output", out], capture_output=True, text=True,
                       timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])

def synthesize(text, mp3):
    """Generate speech for one message.

    The TTS engine inserts multi-second silences and occasional garbled
    words on long multi-sentence input (measured: a 3-sentence text came
    back 27.8 s with a 10.7 s silence). Synthesize sentence by sentence in
    parallel and join with a fixed 0.35 s gap instead (same text: 10.8 s,
    max silence 1.1 s).
    """
    import re
    from concurrent.futures import ThreadPoolExecutor
    parts = [p for p in re.findall(r"[^。！？]+[。！？]?", text) if p.strip()]
    if len(parts) <= 1:
        tts_speak(text, mp3)
        return
    tmp = f"{mp3}.parts"
    os.makedirs(tmp, exist_ok=True)
    def gen(i_part):
        i, part = i_part
        out = f"{tmp}/{i:02d}.mp3"
        tts_speak(part, out)
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
                    "-ac", "1", mp3], check=True, timeout=60)

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
    q = load_queue()
    now = datetime.datetime.now(JST)
    q["messages"].append({"id": mid,
                          "ts": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                          "audio": f"audio/{mid}.mp3", "text": text})
    status, resp = upload_queue(q)
    if status != 200 or not resp.get("ok"):
        print(json.dumps({"ok": False, "stage": "queue", "status": status, "resp": resp}))
        return
    save_queue(q)
    print(json.dumps({"ok": True, "id": mid, "messages": len(q["messages"])}))

def cmd_sync():
    q = load_queue()
    status, resp = upload_queue(q)
    print(json.dumps({"ok": status == 200 and resp.get("ok"), "status": status, "resp": resp}))

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
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "cleanup":
        cmd_cleanup(int(sys.argv[2]) if len(sys.argv) > 2 else 3600)
    else:
        sys.exit(f"unknown command: {cmd}")
