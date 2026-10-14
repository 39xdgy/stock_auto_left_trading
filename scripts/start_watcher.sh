#!/usr/bin/env bash
# Start the watcher detached (it must outlive any single tool call). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p journal
if [[ -f journal/watcher.pid ]] && kill -0 "$(cat journal/watcher.pid)" 2>/dev/null; then
  echo "watcher already running, pid $(cat journal/watcher.pid)"
  exit 0
fi
setsid nohup python3 -m trader.live.watcher --status journal/watcher.status.json --stops journal/stops.json "$@" \
  >> journal/events.jsonl 2>> journal/watcher.err < /dev/null &
echo $! > journal/watcher.pid
echo "watcher started, pid $!"
