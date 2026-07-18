#!/usr/bin/env bash
# Builds the standalone "M3U Player.app". The result needs nothing installed on
# the target Mac — no Python, no VLC, no ffmpeg.
set -euo pipefail
cd "$(dirname "$0")"

VENV="$HOME/Library/Application Support/M3UPlayer/venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Setting up the build environment (first run)…"
  mkdir -p "$HOME/Library/Application Support/M3UPlayer"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
fi
"$VENV/bin/pip" install -q -r requirements.txt pyinstaller

rm -rf build dist
"$VENV/bin/pyinstaller" --noconfirm --clean \
  --distpath dist --workpath build \
  packaging/M3UPlayer.spec

echo
echo "Built: dist/M3U Player.app"
echo "Install with:  cp -R 'dist/M3U Player.app' /Applications/"
echo
echo "The bundle is unsigned, so the first launch on another Mac needs"
echo "right-click -> Open to get past Gatekeeper."
