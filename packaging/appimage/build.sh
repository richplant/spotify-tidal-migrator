#!/usr/bin/env bash
# Builds dist/spotify-tidal-migrator-x86_64.AppImage from the PyInstaller
# onedir bundle. Linux-only (AppImage is a Linux packaging format).
set -euo pipefail

cd "$(dirname "$0")/../.."  # repo root

APP_NAME=spotify-tidal-migrator
APPDIR=build/AppDir
TOOLS_DIR=build/tools
APPIMAGETOOL="${TOOLS_DIR}/appimagetool-x86_64.AppImage"

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin"

echo "==> Building PyInstaller onedir bundle"
pyinstaller --noconfirm --clean --name "${APP_NAME}" --windowed pyinstaller_entry.py
cp -r "dist/${APP_NAME}" "${APPDIR}/usr/bin/${APP_NAME}"

echo "==> Generating icon"
QT_QPA_PLATFORM=offscreen python packaging/appimage/make_icon.py "${APPDIR}/${APP_NAME}.png"

echo "==> Writing desktop file"
cat > "${APPDIR}/${APP_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Spotify to Tidal Migrator
Exec=${APP_NAME}
Icon=${APP_NAME}
Categories=AudioVideo;Audio;Music;
Terminal=false
EOF

echo "==> Writing AppRun"
cat > "${APPDIR}/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\${0}")")"
exec "\${HERE}/usr/bin/${APP_NAME}/${APP_NAME}" "\$@"
EOF
chmod +x "${APPDIR}/AppRun"

echo "==> Fetching appimagetool"
mkdir -p "${TOOLS_DIR}"
if [ ! -x "${APPIMAGETOOL}" ]; then
  curl -fL -o "${APPIMAGETOOL}" \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "${APPIMAGETOOL}"
fi

echo "==> Building AppImage"
mkdir -p dist
# --appimage-extract-and-run: appimagetool is itself an AppImage, which
# normally needs FUSE to mount; this makes it self-extract instead, which
# also works in CI/containers where /dev/fuse isn't available.
ARCH=x86_64 "${APPIMAGETOOL}" --appimage-extract-and-run \
  "${APPDIR}" "dist/${APP_NAME}-x86_64.AppImage"

echo "==> Done: dist/${APP_NAME}-x86_64.AppImage"
