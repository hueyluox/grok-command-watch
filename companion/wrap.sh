#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${HOME}/.grok/command-watch/Grok Command Watch.app"
BIN_DIR="$OUT/Contents/MacOS"
RES_DIR="$OUT/Contents/Resources"
cd "$ROOT/companion"
swift build -c release
mkdir -p "$BIN_DIR" "$RES_DIR"
cp .build/release/command-watch-companion "$BIN_DIR/"
cp app/Info.plist "$OUT/Contents/Info.plist"
echo "wrapped $OUT"
echo "open it once so macOS asks for Bluetooth / Mic / Accessibility"
