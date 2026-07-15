#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# python-vlc normally finds VLC.app on its own. If it can't, uncomment these to
# point it at the installed engine explicitly:
# export DYLD_LIBRARY_PATH="/Applications/VLC.app/Contents/MacOS/lib:${DYLD_LIBRARY_PATH:-}"
# export PYTHON_VLC_LIB_PATH="/Applications/VLC.app/Contents/MacOS/lib/libvlc.dylib"

exec .venv/bin/python -m m3u_player.main
