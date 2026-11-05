#!/bin/bash
# Invoke once from the platform's 20-second hook; never run a sleep loop here.
set -eu
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$script_dir/hook_once.py" "$@" >/dev/null 2>&1
