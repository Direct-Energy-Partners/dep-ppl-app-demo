#!/bin/bash
# Sets up a venv, installs deps, and registers the app to start on every boot.
# Run once: bash install.sh

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

CRON="@reboot sleep 30 && $DIR/start.sh  # DEP_PPL_APP"
( crontab -l 2>/dev/null | grep -v DEP_PPL_APP || true ; echo "$CRON" ) | crontab -

echo "Installed. Reboot to start, or run now: $DIR/start.sh"
