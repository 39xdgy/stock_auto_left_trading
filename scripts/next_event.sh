#!/usr/bin/env bash
# Wait for the next watcher event, print it, exit. Run with Bash run_in_background: the exit wakes Claude.
#   scripts/next_event.sh N [MAX_MINUTES]   # N = event lines already handled (0 at the start of the day)
# Exits with one of:
#   the new event line(s) from journal/events.jsonl
#   {"event": "WATCHER_EXITED", ...}  the watcher process is gone (normal right after EOD / NO_DATA)
#   {"event": "IDLE", ...}            nothing for MAX_MINUTES (default 90): check the status, run again
cd "$(dirname "$0")/.."
N=${1:-0}; MAX=$(( ${2:-90} * 60 )); waited=0
touch journal/events.jsonl
while true; do
  if (( $(wc -l < journal/events.jsonl) > N )); then
    tail -n +"$(( N + 1 ))" journal/events.jsonl
    exit 0
  fi
  if ! kill -0 "$(cat journal/watcher.pid 2>/dev/null || echo 0)" 2>/dev/null; then
    sleep 1
    if (( $(wc -l < journal/events.jsonl) > N )); then continue; fi
    printf '{"event": "WATCHER_EXITED", "stderr_tail": %s}\n' \
      "$(tail -n 3 journal/watcher.err 2>/dev/null | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
    exit 0
  fi
  if (( waited >= MAX )); then
    printf '{"event": "IDLE", "status": %s}\n' "$(cat journal/watcher.status.json 2>/dev/null || echo null)"
    exit 0
  fi
  sleep 5; waited=$(( waited + 5 ))
done
