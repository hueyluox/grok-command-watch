#!/bin/bash
# Install runtime copies from this repo. Does not restart launchd.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${HOME}"
RUN="${HOME_DIR}/.grok/command-watch"
BIN="${RUN}/bin"
HOOKS="${HOME_DIR}/.grok/hooks"
AGENTS="${HOME_DIR}/Library/LaunchAgents"

mkdir -p "$BIN" "$HOOKS" "$RUN" "$AGENTS"

install -m 0755 "$ROOT/host/roster.py" "${RUN}/roster.py"
install -m 0755 "$ROOT/host/launch.sh" "${RUN}/launch.sh"
install -m 0755 "$ROOT/host/keys_daemon.py" "${BIN}/keys_daemon.py"
install -m 0755 "$ROOT/host/ghostty_keys.py" "${BIN}/ghostty_keys.py"
ln -sfn "$ROOT" "${RUN}/src"

# g1=⌘1上  g2=⌘1下  g3=⌘2上  g4=⌘2下
# g1r/g2r/… still exist for extra panes (3L/3R/4L/4R).
for slot in 1L 1R 2L 2R 3L 3R 4L 4R; do
  case "$slot" in
    1L) cmd=g1 ;;
    1R) cmd=g2 ;;
    2L) cmd=g3 ;;
    2R) cmd=g4 ;;
    3L) cmd=g3l ;;
    3R) cmd=g3r ;;
    4L) cmd=g4l ;;
    4R) cmd=g4r ;;
  esac
  cat > "$BIN/$cmd" <<EOF
#!/bin/bash
exec "${RUN}/launch.sh" $slot "\$@"
EOF
  chmod 0755 "$BIN/$cmd"
done
# keep old extra names so existing shells still work
for pair in g1r:1R g2r:2R; do
  cmd="${pair%%:*}"
  slot="${pair##*:}"
  cat > "$BIN/$cmd" <<EOF
#!/bin/bash
exec "${RUN}/launch.sh" $slot "\$@"
EOF
  chmod 0755 "$BIN/$cmd"
done

python3 - "$ROOT/host/hooks/command-watch.json" "$HOOKS/command-watch.json" "$HOME_DIR" <<'PY'
import json, sys
from pathlib import Path
src, dest, home = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = src.read_text(encoding="utf-8").replace("HOME", home)
dest.write_text(text)
print(f"wrote {dest}")
PY

subst() {
  local src="$1" dest="$2"
  sed "s|HOME|${HOME_DIR}|g" "$src" > "$dest"
}
subst "$ROOT/host/launchd/local.grok-command-watch.plist" "${AGENTS}/local.grok-command-watch.plist"
subst "$ROOT/host/launchd/local.grok-command-watch-keys.plist" "${AGENTS}/local.grok-command-watch-keys.plist"
echo "wrote LaunchAgents (not bootstrapped)"

ZSHRC="${HOME_DIR}/.zshrc"
LINE='export PATH="$HOME/.grok/command-watch/bin:$PATH"'
if [[ -f "$ZSHRC" ]] && ! grep -F "$LINE" "$ZSHRC" >/dev/null 2>&1; then
  printf '\n# Grok Command Watch launchers\n%s\n' "$LINE" >> "$ZSHRC"
  echo "appended PATH to ~/.zshrc"
fi

echo "install ok"
echo "  project: $ROOT"
echo "  runtime: $RUN"
echo "  launch:  g1 g2 g3 g4   (⌘1上 / ⌘1下 / ⌘2上 / ⌘2下)"
echo "  companion wrap: bash $ROOT/companion/wrap.sh"
echo "  load agents (once): launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/local.grok-command-watch.plist"
echo "                     launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/local.grok-command-watch-keys.plist"
