#!/usr/bin/env python3
"""Summarize one PocReceiver JSONL log; never estimates unsent seqs."""
import argparse
import datetime as dt
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    rows = []
    with args.file.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            try:
                row = json.loads(line)
                arrival = dt.datetime.fromisoformat(row["arrival"].replace("Z", "+00:00"))
                rows.append((arrival, int(row["seq"]), int(row["t"])))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise SystemExit(f"line {line_no}: invalid record: {exc}")
    if not rows:
        raise SystemExit("No records")
    missing = 0
    observed = 1
    resets = duplicates = 0
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        gap = (current[0] - previous[0]).total_seconds()
        gaps.append(gap)
        diff = current[1] - previous[1]
        if diff > 0:
            missing += diff - 1
            observed += 1
        elif diff == 0:
            duplicates += 1
        else:
            resets += 1
            observed += 1
    print(f"Records: {len(rows)}, first: {rows[0][0].isoformat()}, last: {rows[-1][0].isoformat()}")
    print(f"Missing within increasing seq runs: {missing}; observed unique positions: {observed}")
    print(f"Missing rate within observed seq ranges: {missing / (missing + observed):.2%}")
    print(f"Duplicate adjacent seq: {duplicates}; seq decreases/resets: {resets}")
    if gaps:
        print(f"Max arrival gap: {max(gaps):.1f} s; gaps >30 s: {sum(g > 30 for g in gaps)}")
        for previous, current in zip(rows, rows[1:]):
            gap = (current[0] - previous[0]).total_seconds()
            if gap > 30:
                print(f"  {previous[0].isoformat()} -> {current[0].isoformat()}: {gap:.1f}s; timer advanced {(current[2]-previous[2])/1000:.1f}s")
    print("Missing rate excludes skipped send opportunities, before first/after last message, and pauses. Inspect >30s gaps for pauses and clock changes.")


if __name__ == "__main__":
    main()
