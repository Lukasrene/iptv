#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Self-contained virtualenv inside the project. Keep this project on local disk
# (not iCloud Drive) — iCloud evicts binary files to the cloud, which breaks
# Qt/VLC library loading.
VENV=".venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Setting up the environment (first run)…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

# python-vlc normally finds VLC.app on its own. If it can't, uncomment these to
# point it at the installed engine explicitly:
# export DYLD_LIBRARY_PATH="/Applications/VLC.app/Contents/MacOS/lib:${DYLD_LIBRARY_PATH:-}"
# export PYTHON_VLC_LIB_PATH="/Applications/VLC.app/Contents/MacOS/lib/libvlc.dylib"

exec .venv/bin/python -m m3u_player.main
