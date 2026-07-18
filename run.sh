#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# This repo may live in an iCloud-synced folder. iCloud evicts binary files to
# the cloud, which breaks Qt library loading — so the virtualenv is kept on
# LOCAL disk (Application Support), never inside the synced project folder.
VENV="$HOME/Library/Application Support/M3UPlayer/venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Setting up the environment (first run)…"
  mkdir -p "$HOME/Library/Application Support/M3UPlayer"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

# This is the development entry point. For the standalone bundle see build_app.sh.
exec "$VENV/bin/python" -m m3u_player.main
