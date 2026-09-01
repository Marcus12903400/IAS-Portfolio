#!/bin/bash
# AutoDeck - one click. Opens the review UI in your browser.
# Everything is found relative to THIS folder.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export AUTODECK2_ROOT="$HERE/engine"
export AUTODECK_V1_ROOT="$HERE/engine-v1"
APP_DIR="$HERE/app"
VENV="$HERE/.venv"
PY="$VENV/bin/python"
export PYTHONPATH="$AUTODECK_V1_ROOT/src:$AUTODECK2_ROOT:$APP_DIR"

echo "============================================================"
echo "  AutoDeck - starting"
echo "============================================================"
echo

for p in "$APP_DIR/autodeck_app/__main__.py" "$AUTODECK2_ROOT/autodeck2" "$AUTODECK_V1_ROOT/src/autodeck"; do
  if [ ! -e "$p" ]; then
    echo "Missing part of the bundle: $p"; read -r -p "Press return to close." _; exit 2
  fi
done

if [ ! -x "$PY" ]; then
  echo "First-time setup. This happens once and needs the internet (a few minutes)."
  echo
  BASE=""
  for c in python3.12 python3.13 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,12) else 1)' >/dev/null 2>&1; then
      BASE="$c"; break
    fi
  done
  if [ -z "$BASE" ]; then
    echo "Python 3.12+ was not found. Install it from https://www.python.org/downloads/"
    read -r -p "Press return to close." _; exit 2
  fi
  echo "Using Python: $(command -v "$BASE")"
  "$BASE" -m venv "$VENV" || { echo "Could not create $VENV"; read -r -p "Press return." _; exit 2; }
  "$PY" -m pip install --disable-pip-version-check --upgrade pip >/dev/null
  echo "  [1/3] analysis engine (v1)"
  "$PY" -m pip install --disable-pip-version-check -e "$AUTODECK_V1_ROOT[rhino]" || { echo "Setup failed - delete $VENV and try again."; read -r -p "Press return." _; exit 2; }
  echo "  [2/3] outline / auto-fit engine"
  "$PY" -m pip install --disable-pip-version-check --no-deps -e "$AUTODECK2_ROOT" || { echo "Setup failed - delete $VENV and try again."; read -r -p "Press return." _; exit 2; }
  echo "  [3/3] review app"
  "$PY" -m pip install --disable-pip-version-check "libigl==2.6.1" -e "$APP_DIR" || { echo "Setup failed - delete $VENV and try again."; read -r -p "Press return." _; exit 2; }
  echo
  echo "Setup complete."
  echo
fi

echo "Opening AutoDeck in your browser..."
echo "Leave this window open - closing it stops AutoDeck."
echo
"$PY" -m autodeck_app "$@"
