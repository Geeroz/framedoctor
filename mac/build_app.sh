#!/bin/bash
# Build FrameDoctor.app — a self-contained drag-and-drop app (Python, OpenCV and FFmpeg bundled).
# Run on a Mac:   ./mac/build_app.sh
# Output:         dist/FrameDoctor.app  and  dist/FrameDoctor-<version>-macOS-<arch>.zip
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

[[ "$(uname)" == "Darwin" ]] || { echo "Run this on macOS."; exit 1; }
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "python3 not found. Install Xcode Command Line Tools: xcode-select --install"; exit 1; }

ARCH="$(uname -m)"
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' framedoctor.py)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
APP="$DIST/FrameDoctor.app"

echo "==> FrameDoctor $VER for $ARCH"
rm -rf "$BUILD" "$APP"
mkdir -p "$BUILD" "$DIST"

echo "==> Creating build environment"
"$PY" -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install -q --upgrade pip
"$BUILD/venv/bin/pip" install -q -r requirements.txt pyinstaller

echo "==> Bundling engine"
"$BUILD/venv/bin/pyinstaller" --noconfirm --log-level WARN --onedir --name framedoctor \
  --collect-binaries imageio_ffmpeg --collect-data imageio_ffmpeg \
  --distpath "$BUILD/pyi" --workpath "$BUILD/work" --specpath "$BUILD" \
  framedoctor.py

echo "==> Building app"
osacompile -o "$APP" mac/FrameDoctor.applescript
cp -R "$BUILD/pyi/framedoctor" "$APP/Contents/Resources/"

PL="$APP/Contents/Info.plist"
setplist() { /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PL"; }
setplist CFBundleIdentifier com.aigentools.framedoctor
setplist CFBundleShortVersionString "$VER"
setplist CFBundleVersion "$VER"

if [[ -f mac/icon.png ]]; then
  echo "==> Icon"
  ICONSET="$BUILD/FrameDoctor.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s mac/icon.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) mac/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  # osacompile droplets point at droplet.icns plus an Assets.car that wins over it;
  # swap in our icon and drop the asset catalog.
  RES="$APP/Contents/Resources"
  iconutil -c icns "$ICONSET" -o "$RES/FrameDoctor.icns"
  rm -f "$RES/Assets.car" "$RES/droplet.icns" "$RES/applet.icns"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$PL" 2>/dev/null || true
  setplist CFBundleIconFile FrameDoctor
  touch "$APP"
fi

echo "==> Signing (ad-hoc)"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"

echo "==> Smoke test"
"$APP/Contents/Resources/framedoctor/framedoctor" --version

ZIP="$DIST/FrameDoctor-$VER-macOS-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
echo
echo "Done:"
echo "  $APP"
echo "  $ZIP   <- share this (or attach it to a GitHub release)"
