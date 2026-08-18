#!/bin/bash
# Bind this pane to a Command slot, then exec grok. Usage: launch.sh 1L
set -euo pipefail
SLOT="${1:-}"
case "$SLOT" in
  1L|1R|2L|2R|3L|3R|4L|4R) ;;
  *)
    echo "usage: launch.sh 1L|1R|2L|2R|3L|3R|4L|4R" >&2
    exit 2
    ;;
esac

DIR="${HOME}/.grok/command-watch"
mkdir -p "$DIR"
export GROK_COMMAND_SLOT="$SLOT"

TTY="$(tty 2>/dev/null || true)"
# Keep a unique window title so the watch can raise this pane, not just ⌘N.
printf '\033]0;Grok-%s\007' "$SLOT"

python3 - "$DIR/slots.json" "$SLOT" "$$" "$TTY" <<'PY'
import json, sys, time
from pathlib import Path
path = Path(sys.argv[1])
slot, pid, tty = sys.argv[2], int(sys.argv[3]), sys.argv[4]
data = {"slots": {}}
if path.exists():
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        data = {"slots": {}}
data.setdefault("slots", {})
data["slots"][slot] = {
    "slot": slot,
    "pid": pid,
    "tty": tty or None,
    "started_at": time.time(),
}
path.write_text(json.dumps(data, indent=2) + "\n")
PY

exec grok "${@:2}"
