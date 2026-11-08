#!/usr/bin/env bash
# Build neopulse_fw.mpy and copy the firmware to a mounted NeoPulse/CIRCUITPY drive.
# Usage: tools/deploy.sh [/Volumes/NEOPULSE]   (defaults to NEOPULSE, then CIRCUITPY)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MPY="$ROOT/tools/mpy-cross"
DEST="${1:-}"
if [ -z "$DEST" ]; then
  for v in /Volumes/NEOPULSE /Volumes/CIRCUITPY /media/$USER/NEOPULSE /media/$USER/CIRCUITPY; do
    [ -d "$v" ] && DEST="$v" && break
  done
fi
[ -d "$DEST" ] || { echo "no NEOPULSE/CIRCUITPY drive found"; exit 1; }
mkdir -p "$ROOT/firmware/dist" "$DEST/lib" "$DEST/boards"
"$MPY" -o "$ROOT/firmware/dist/neopulse_fw.mpy" "$ROOT/firmware/neopulse_fw.py"
cp "$ROOT/firmware/dist/neopulse_fw.mpy" "$DEST/lib/neopulse_fw.mpy"
cp "$ROOT/firmware/code.py" "$DEST/code.py"
cp "$ROOT/firmware/boot.py" "$DEST/boot.py"
[ -f "$DEST/boards/profile.py" ] || cp "$ROOT/firmware/boards/profile.py" "$DEST/boards/profile.py"
sync
echo "deployed to $DEST ($(stat -f%z "$ROOT/firmware/dist/neopulse_fw.mpy" 2>/dev/null || stat -c%s "$ROOT/firmware/dist/neopulse_fw.mpy") bytes mpy)"
