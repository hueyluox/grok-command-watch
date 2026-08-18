#!/bin/bash
set -euo pipefail
APP="${HOME}/.grok/command-watch/Grok Command Watch.app"
LOG="${HOME}/.grok/command-watch/companion.log"
mkdir -p "${HOME}/.grok/command-watch"
: >> "$LOG"
# relaunch so TCC treats it as the .app, not a naked binary
killall command-watch-companion 2>/dev/null || true
open -n "$APP" --args --watch --verbose
echo "started $APP"
echo "log: $LOG"
