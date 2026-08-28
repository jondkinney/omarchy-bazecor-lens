#!/usr/bin/env bash
# Install the Bazecor build this plugin was tested against.
#
# The version and digest below are pinned in this repository, so what gets run
# is decided by this reviewed file rather than by whatever a release currently
# points at. The download is rejected unless its SHA-256 matches, before it is
# ever made executable.
#
#   ./install-bazecor.sh [path-to-AppImage]
#
# With a path, that file is verified against the same digest and installed
# without downloading anything.
set -euo pipefail

BAZECOR_VERSION="v1.10.0-wayland.1"
BAZECOR_ASSET="Bazecor-1.10.0-x64.AppImage"
BAZECOR_SHA256="4929784eb1874f9b758b6a02a2ede0160ad48a4058fc8addd013ded074b2933a"
BAZECOR_URL="https://github.com/jondkinney/Bazecor/releases/download/${BAZECOR_VERSION}/${BAZECOR_ASSET}"

# GitHub serves release assets from its own CDN, so a redirect is expected —
# but only to these hosts.
ALLOWED_HOSTS="github.com objects.githubusercontent.com release-assets.githubusercontent.com"

APP_DIR="$HOME/.local/share/bazecor"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/bazecor.desktop"

die() { echo "install-bazecor: $*" >&2; exit 1; }

verify() { # $1 = file
  local actual
  actual=$(sha256sum -- "$1" | cut -d' ' -f1)
  [[ $actual == "$BAZECOR_SHA256" ]] || die "digest mismatch
  expected $BAZECOR_SHA256
  got      $actual
Refusing to install. Delete the file and try again, and if it keeps failing,
do not run it."
  echo "  digest verified"
}

host_allowed() { # $1 = url
  local host
  host=${1#*://}; host=${host%%/*}; host=${host%%:*}
  for allowed in $ALLOWED_HOSTS; do [[ $host == "$allowed" ]] && return 0; done
  return 1
}

SRC=${1:-}
TMP=""
if [[ -n $SRC ]]; then
  [[ -f $SRC ]] || die "not a file: $SRC"
else
  command -v curl >/dev/null || die "curl is required to download"
  TMP=$(mktemp -d)
  trap 'rm -rf -- "$TMP"' EXIT
  echo "Downloading $BAZECOR_ASSET $BAZECOR_VERSION"
  # --proto '=https' refuses to be redirected onto a plaintext scheme.
  # Firm timeouts so an unavailable or stalled asset fails instead of hanging:
  # 15s to connect, and the transfer is abandoned if it manages under 1 KB/s
  # for a minute. --max-time is a generous hard ceiling for a 150 MB file on a
  # slow link rather than a normal-case limit.
  final=$(curl --proto '=https' --tlsv1.2 -fL --max-redirs 5 \
      --connect-timeout 15 --speed-limit 1024 --speed-time 60 --max-time 3600 \
      -o "$TMP/$BAZECOR_ASSET" -w '%{url_effective}' "$BAZECOR_URL") \
    || die "download failed or timed out"
  host_allowed "$final" || die "download ended up on an unexpected host: $final"
  SRC="$TMP/$BAZECOR_ASSET"
fi

verify "$SRC"

mkdir -p -- "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
install -m 755 -- "$SRC" "$APP_DIR/Bazecor.AppImage"
ln -sfn -- "$APP_DIR/Bazecor.AppImage" "$BIN_DIR/bazecor"

# Written to a temp file in the same directory and renamed into place. A plain
# redirect to the destination would follow a symlink sitting at that path and
# truncate whatever it points at.
[[ -d $DESKTOP_DIR && ! -L $DESKTOP_DIR ]] || die "$DESKTOP_DIR is not a real directory"
DESKTOP_TMP=$(mktemp -- "$DESKTOP_DIR/.bazecor.desktop.XXXXXXXX")
trap 'rm -f -- "$DESKTOP_TMP"' EXIT
chmod 644 -- "$DESKTOP_TMP"

cat > "$DESKTOP_TMP" <<EOF
[Desktop Entry]
Type=Application
Name=Bazecor
GenericName=Keyboard Configurator
Comment=Configure your Dygma keyboard
Exec=$APP_DIR/Bazecor.AppImage
Icon=input-keyboard
Terminal=false
Categories=Utility;
Keywords=dygma;keyboard;keymap;layout;macro;lens;
StartupNotify=true
StartupWMClass=Bazecor
Actions=Hidden;ToggleLens;

[Desktop Action Hidden]
Name=Start in tray (Layer Lens only)
Exec=$APP_DIR/Bazecor.AppImage --hidden

[Desktop Action ToggleLens]
Name=Toggle Layer Lens
Exec=$APP_DIR/Bazecor.AppImage --toggle-lens
EOF

mv -f -- "$DESKTOP_TMP" "$DESKTOP_FILE"
trap - EXIT

command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

cat <<EOF

Done.

  binary    $APP_DIR/Bazecor.AppImage
  on PATH   $BIN_DIR/bazecor
  launcher  $DESKTOP_FILE

Layer Lens needs HID access. Start Bazecor once with the keyboard plugged in
and accept the udev prompt, then replug the keyboard.

To remove:
  rm -rf $APP_DIR $BIN_DIR/bazecor $DESKTOP_FILE
EOF
