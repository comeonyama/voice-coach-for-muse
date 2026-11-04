"""Publisher subprocess boundary shared by monitoring and recovery."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from runtime import error_info


def publisher_call(cfg, *args):
    env = os.environ.copy()
    if cfg.get("queue_json"):
        env["VCM_QUEUE_DIR"] = str(Path(cfg["queue_json"]).resolve().parent)
    process = None
    try:
        process = subprocess.Popen(
            ["python3", cfg["publish_script"], *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, start_new_session=True)
        stdout, _ = process.communicate(timeout=120)
        try:
            result = json.loads(stdout)
        except (ValueError, TypeError):
            result = {}
        if not isinstance(result, dict):
            result = {}
        # Do not retain stderr, arbitrary response bodies or exception messages.
        return {"ok": process.returncode == 0 and result.get("ok") is True,
                "stage": args[0], "returncode": process.returncode,
                "error_type": result.get("error_type") if isinstance(result.get("error_type"), str) else None}
    except (OSError, subprocess.SubprocessError) as error:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        return {"ok": False, **error_info(error, args[0])}


def publish_retry(cfg, *args, attempts=None):
    attempts = max(1, int(attempts if attempts is not None else cfg.get("say_attempts", 3)))
    for attempt in range(attempts):
        result = publisher_call(cfg, *args)
        if result["ok"]:
            return result
        if attempt + 1 < attempts:
            time.sleep(float(cfg.get("say_retry_backoff_sec", 5)) * 2 ** attempt)
    return result
