#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python monitor.py --loop "$@"
fi
exec python3 monitor.py --loop "$@"
