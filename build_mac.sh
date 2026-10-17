#!/bin/bash
# Builds 逐字.app for this Mac's processor and packs it into a disk image.
#
#   ./build_mac.sh                        # with .venv-mac/bin/python
#   PYTHON=/path/to/python ./build_mac.sh
#
# Output: dist/Zhuzi-mac-apple-silicon.dmg or dist/Zhuzi-mac-intel.dmg (no
# version in the name: README links to releases/latest/download/<name>), and
# the app itself in build/mac/逐字.app.
#
# Environment (first time), with Python 3.12 from python.org's macOS installer
# (its binaries run on old systems; Homebrew's or a self-built one may not):
#   python3.12 -m venv .venv-mac
#   .venv-mac/bin/python -m pip install -r requirements.txt
#
# The app is signed ad hoc, not with an Apple Developer ID. Apple silicon needs
# at least that to run it; the first launch still asks the user to allow it in
# System Settings, as the README explains.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$PWD"
PY="${PYTHON:-$HERE/.venv-mac/bin/python}"
ARCH=$(uname -m)
case "$ARCH" in
  arm64) FLAVOR=apple-silicon ;;
  x86_64) FLAVOR=intel ;;
  *) echo "unsupported processor: $ARCH" >&2; exit 1 ;;
esac
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' src/core.py)
WORK="$HERE/build/mac"
APP="$WORK/逐字.app"
DMG="$HERE/dist/Zhuzi-mac-$FLAVOR.dmg"
export PYINSTALLER_CONFIG_DIR="$WORK/pyi-config"  # keep PyInstaller's cache in the project

"$PY" assets/make_icon.py

rm -rf "$WORK/dist" "$APP"
# --target-arch also thins universal2 libraries (Qt ships them) to this processor.
"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --name Zhuzi \
  --target-arch "$ARCH" \
  --icon "$HERE/assets/icon.icns" \
  --osx-bundle-identifier io.github.ellenzhang708.zhuzi \
  --add-data "$HERE/assets/icon.png:assets" \
  --add-data "$HERE/assets/silero_vad_v6.onnx:assets" \
  --paths "$HERE/src" \
  --collect-all sherpa_onnx \
  --exclude-module tkinter \
  --exclude-module PIL \
  --distpath "$WORK/dist" \
  --workpath "$WORK/work" \
  --specpath "$WORK" \
  "$HERE/src/app.py"
# The executable inside stays Zhuzi; only the name Finder shows changes.
mv "$WORK/dist/Zhuzi.app" "$APP"

# Drop what a widgets app never loads: Qt's network plugins, and every Qt
# translation but Simplified Chinese. PyInstaller splits a bundle between
# Frameworks (code) and Resources (data), with links across, so look in both.
for part in Frameworks Resources; do
  qt="$APP/Contents/$part/PySide6/Qt"
  rm -rf "$qt/plugins/networkinformation" "$qt/plugins/tls"
  if [ -d "$qt/translations" ] && [ ! -L "$qt/translations" ]; then
    find "$qt/translations" -mindepth 1 \( -type f -o -type l \) ! -name '*_zh_CN.qm' -delete
  fi
done
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"

# The oldest macOS every bundled binary supports. Each must run on macOS
# $FLOOR (PySide6 6.12's own floor): a Python built for the build machine's
# newer system would otherwise raise it silently (use python.org's installer,
# not a locally compiled Python). otool fails on files that aren't code,
# hence the `|| true`.
FLOOR=14.0
REPORT="$WORK/minos.txt"
: > "$REPORT"
while IFS= read -r -d '' f; do
  v=$(otool -l "$f" 2>/dev/null | awk '$1 == "minos" { print $2; exit }') || true
  if [ -n "$v" ]; then printf '%s\t%s\n' "$v" "${f#"$APP"/}" >> "$REPORT"; fi
done < <(find "$APP/Contents" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) -print0)
MINOS=$(cut -f1 "$REPORT" | sort -t. -k1,1n -k2,2n | tail -1)
TOO_NEW=$(awk -F'\t' -v floor="$FLOOR" 'function n(s, p) { split(s, p, "."); return p[1] * 1000 + p[2] }
  n($1) > n(floor)' "$REPORT")
if [ -n "$TOO_NEW" ]; then
  echo "These need a newer macOS than $FLOOR:" >&2
  echo "$TOO_NEW" >&2
  exit 1
fi

PLIST="$APP/Contents/Info.plist"
plist() { plutil -replace "$1" "$2" "$3" "$PLIST"; }
plist CFBundleName -string "逐字"
plist CFBundleDisplayName -string "逐字"
plist CFBundleShortVersionString -string "$VERSION"
plist CFBundleVersion -string "$VERSION"
plist LSMinimumSystemVersion -string "${MINOS:-$FLOOR}"
plist LSApplicationCategoryType -string public.app-category.productivity
plist NSHumanReadableCopyright -string "MIT License · github.com/EllenZhang708/zhuzi"
plist NSHighResolutionCapable -bool YES  # without it, Retina screens get a blurry upscale
plist NSPrincipalClass -string NSApplication
# Shown when macOS asks whether 逐字 may use these folders.
plist NSDocumentsFolderUsageDescription -string "逐字把誊好的文稿存进「文稿」里的“逐字稿”文件夹。"
plist NSDesktopFolderUsageDescription -string "读取你放在桌面上的音频，或把文稿存到桌面。"
plist NSDownloadsFolderUsageDescription -string "读取你下载的音频，或把文稿存到「下载」。"

# Re-sign after the edits above; a broken signature reads as "damaged" on Apple silicon.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

STAGE="$WORK/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE" "$HERE/dist"
ditto "$APP" "$STAGE/逐字.app"
ln -s /Applications "$STAGE/应用程序"
for attempt in 1 2 3; do  # hdiutil is sometimes briefly "busy" on build machines
  if hdiutil create -volname "逐字" -srcfolder "$STAGE" -fs HFS+ -format ULMO -ov "$DMG"; then break; fi
  [ "$attempt" = 3 ] && exit 1
  sleep 5
done

echo "Built $APP ($(du -sh "$APP" | cut -f1), macOS $MINOS or later) and $DMG ($(du -h "$DMG" | cut -f1))"
