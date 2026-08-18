#!/bin/bash
# Optional wrapper. Just starts grok — the watch finds the pane itself.
set -euo pipefail
if [[ "${1:-}" =~ ^(1L|1R|2L|2R|3L|3R|4L|4R)$ ]]; then
  shift
fi
exec grok "$@"
