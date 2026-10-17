#!/bin/bash
# Checks a freshly built 逐字.app (from build_mac.sh) the way a user's Mac
# would run it: the signature holds, the frozen app downloads the offline model
# over HTTPS, decodes, finds speech and writes a transcript, and every screen
# renders. Everything it produces lands in build/mac/check.
#
#   bash tests/check_mac_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APP="build/mac/逐字.app"
BIN="$APP/Contents/MacOS/Zhuzi"
OUT="$PWD/build/mac/check"
LOGS="$HOME/Library/Application Support/Zhuzi/logs"
PY="${PYTHON:-$PWD/.venv-mac/bin/python}"
rm -rf "$OUT"
mkdir -p "$OUT"

# Run a command, giving up after N seconds (macOS has no `timeout`).
limit() { perl -e 'alarm shift; exec @ARGV' "$@"; }

echo "== signature and bundle"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv "$APP" 2>&1 | tee "$OUT/codesign.txt"
plutil -p "$APP/Contents/Info.plist" | tee "$OUT/Info.plist.txt"
file "$BIN" | tee "$OUT/arch.txt"
du -sh "$APP" dist/*.dmg | tee "$OUT/sizes.txt"

echo "== the disk image"
MOUNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MOUNT" dist/*.dmg >/dev/null
ls -la "$MOUNT" | tee "$OUT/dmg-contents.txt"
test -x "$MOUNT/逐字.app/Contents/MacOS/Zhuzi"
test -L "$MOUNT/应用程序"
hdiutil detach "$MOUNT" >/dev/null

echo "== offline transcription in the frozen app"
# A few sentences read by the system voice (Chinese if this Mac has one).
# The list is captured first: piped into `grep -q`, `say` would be cut off,
# and under pipefail a found voice would read as a failure.
voices=$(say -v '?')
if grep -q '^Tingting ' <<<"$voices"; then
  say -v Tingting -o "$OUT/speech.aiff" "大家好，欢迎收听这一期节目。今天我们来聊一聊，怎样把声音誊成文字。谢谢你的收听，我们下期再见。"
else
  say -o "$OUT/speech.aiff" "Hello everyone, and welcome to this episode. Today we talk about turning speech into text. Thanks for listening."
fi
status=0
limit 900 "$BIN" --selftest "$OUT/speech.aiff" --engine local --out "$OUT/transcripts" || status=$?
cp "$LOGS/selftest-result.json" "$LOGS/selftest.log" "$OUT/" 2>/dev/null || true
cat "$OUT/selftest.log" 2>/dev/null || true
cat "$OUT/selftest-result.json"
test "$status" = 0
"$PY" - "$OUT/selftest-result.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
assert r["ok"] and r["segments"] > 0, r
assert "certifi" in (r.get("cafile") or ""), f"HTTPS is not using the bundled certificates: {r.get('cafile')}"
print("transcribed", r["segments"], "segments in", r["seconds"], "s")
EOF
cat "$OUT"/transcripts/*.md

echo "== every screen, in light and dark"
limit 300 "$BIN" --screenshot "$OUT/screens"
ls "$OUT/screens"
test "$(ls "$OUT/screens" | wc -l)" -ge 10

echo "all checks passed"
