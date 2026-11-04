#!/bin/bash
# Takes every documentation screenshot (English and German) from a demo
# instance with made-up data and writes them to docs/images as WebP.
# Nothing here touches a real printer or real state.
#
# usage: scripts/docs_screenshots/run.sh
# needs: PYTHON - a Python with the app's requirements (default: python3)
#        node with the playwright package and its Chromium
#        (PLAYWRIGHT_MODULE / SYSLIBS, see shoot.js)
# Port 5000 must be free; the browser maps receiptpi.local to it, so the
# screenshots show the address users will see.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
PY=${PYTHON:-python3}
WORK=$(mktemp -d); RAW="$WORK/raw"
trap 'rm -rf "$WORK"' EXIT
"$PY" -I -B "$HERE/make_picture.py" "$WORK/demo-picture.jpg"
for lang in en de; do
  "$PY" -I -B "$HERE/demo_server.py" "$WORK/$lang" "$REPO" "$lang" 5000 > "$WORK/server_$lang.log" 2>&1 &
  for _ in $(seq 1 30); do curl -sf http://127.0.0.1:5000/health > /dev/null && break; sleep 1; done
  node "$HERE/shoot.js" "$lang" "$RAW" "$WORK/demo-picture.jpg" || { echo "shoot $lang failed, see $WORK/server_$lang.log"; kill "$(cat "$WORK/$lang/server.pid")"; exit 1; }
  kill "$(cat "$WORK/$lang/server.pid")"; sleep 1
done
"$PY" -I -B "$HERE/convert.py" "$RAW" "$REPO/docs/images"
