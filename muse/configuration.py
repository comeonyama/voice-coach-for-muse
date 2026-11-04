"""Validate monitor configuration without changing existing key semantics."""
import math


def validate_config(cfg):
    def fail(key, expected):
        raise ValueError(f"config.{key}: expected {expected}")

    def number(key, value, minimum=0, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(key, "number")
        if not math.isfinite(value) or value < minimum or (integer and not isinstance(value, int)):
            fail(key, f"{'integer' if integer else 'number'} >= {minimum}")

    if not isinstance(cfg, dict):
        fail("", "object")
    for key in ("state_dir", "poll_script", "publish_script"):
        if not isinstance(cfg.get(key), str) or not cfg[key]:
            fail(key, "nonempty string")
    for key in ("queue_json", "stock_file", "start_announce_text", "closing_text", "no_live_closing_text"):
        if key in cfg and cfg[key] is not None and not isinstance(cfg[key], str):
            fail(key, "string")
    for key in ("audio", "tts_fallback"):
        if key in cfg and not isinstance(cfg[key], bool):
            fail(key, "boolean")
    for key in ("say_attempts", "closing_attempts", "poll_fail_abort", "max_messages"):
        if key in cfg:
            number(key, cfg[key], 1, integer=True)
    for key in ("poll_interval_sec", "send_interval_sec", "stale_finish_sec", "max_duration_min"):
        if key in cfg:
            number(key, cfg[key], 0.001)
    for key in ("say_retry_backoff_sec", "fast_advice_cooldown_sec"):
        if key in cfg:
            number(key, cfg[key])
    for key in ("progress_every", "motivational_every"):
        if key in cfg:
            number(key, cfg[key], integer=True)
    if "timezone_offset_hours" in cfg:
        number("timezone_offset_hours", cfg["timezone_offset_hours"], -23.99)
        if cfg["timezone_offset_hours"] >= 24:
            fail("timezone_offset_hours", "number less than 24")
    if cfg.get("send_mode", "per_km") not in ("per_km", "interval"):
        fail("send_mode", "per_km or interval")
    bands = cfg.get("hr_bands", [])
    if not isinstance(bands, list):
        fail("hr_bands", "array of {max, label}")
    previous = -1
    for i, band in enumerate(bands):
        key = f"hr_bands[{i}]"
        if not isinstance(band, dict) or not isinstance(band.get("label"), str):
            fail(key, "object with string label")
        maximum = band.get("max")
        if maximum is None:
            if i != len(bands) - 1:
                fail(key + ".max", "null only in last band")
        else:
            number(key + ".max", maximum)
            if maximum <= previous:
                fail(key + ".max", "strictly increasing value")
            previous = maximum
    marks = cfg.get("milestones_km", [])
    if not isinstance(marks, list):
        fail("milestones_km", "array")
    for mark in marks:
        number("milestones_km", mark, 0.001)
    for key in ("target_pace", "hydration", "close"):
        if cfg.get(key) is not None and not isinstance(cfg[key], dict):
            fail(key, "object")
    target = cfg.get("target_pace") or {}
    if target.get("fast_limit_sec") is not None:
        number("target_pace.fast_limit_sec", target["fast_limit_sec"])
    hydration = cfg.get("hydration") or {}
    if "enabled" in hydration and not isinstance(hydration["enabled"], bool):
        fail("hydration.enabled", "boolean")
    for key, minimum in (("every_min", 0.001), ("min_run_min", 0)):
        if key in hydration:
            number("hydration." + key, hydration[key], minimum)
    close = cfg.get("close") or {}
    if "wait_before_standby_sec" in close:
        number("close.wait_before_standby_sec", close["wait_before_standby_sec"])
    if "standby_session" in close and (not isinstance(close["standby_session"], str) or not close["standby_session"]):
        fail("close.standby_session", "nonempty string")
    return cfg
