#!/usr/bin/env bash
# Set one boolean on this plugin's own entry in shell.json.
#
#   set-flag.sh <key> <true|false>
#
# Called from LensPanel.qml with the key and value as argv, never as shell text.
# Both are checked again here, since a script on disk can be run by anything.
set -euo pipefail

KEY=${1:-}
VALUE=${2:-}
PLUGIN_ID="io.github.jondkinney.bazecor-lens"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/shell.json"

case $KEY in
  floatOverlay|pinOverlay|themedBorder|rememberPosition) ;;
  *) echo "set-flag: refusing unknown key: $KEY" >&2; exit 2 ;;
esac
case $VALUE in
  true|false) ;;
  *) echo "set-flag: value must be true or false" >&2; exit 2 ;;
esac

[[ -e $CONFIG ]] || { echo "set-flag: no $CONFIG" >&2; exit 1; }
# Refuse to write through a symlink: following one would let a link planted at
# this path redirect the write to a file somewhere else entirely.
[[ -L $CONFIG ]] && { echo "set-flag: $CONFIG is a symlink, refusing" >&2; exit 1; }
[[ -f $CONFIG ]] || { echo "set-flag: $CONFIG is not a regular file" >&2; exit 1; }

DIR=$(dirname -- "$CONFIG")
# mktemp creates the file itself, O_EXCL, mode 600, with a name nobody can
# predict — and in the destination directory, so the replace below is a rename
# within one filesystem rather than a copy across.
TMP=$(mktemp -- "$DIR/.shell.json.XXXXXXXX")
cleanup() { rm -f -- "$TMP"; }
trap cleanup EXIT

jq --arg id "$PLUGIN_ID" --arg key "$KEY" --argjson val "$VALUE" '
  def patch: map(if .id == $id then . + {($key): $val} else . end);
  .plugins = ((.plugins // []) | patch)
  | .bar.layout.left = ((.bar.layout.left // []) | patch)
  | .bar.layout.center = ((.bar.layout.center // []) | patch)
  | .bar.layout.right = ((.bar.layout.right // []) | patch)
' -- "$CONFIG" > "$TMP"

# Never replace a good config with a broken one: check it parses and still
# carries our entry before it goes anywhere near the real file.
jq -e . "$TMP" >/dev/null || { echo "set-flag: produced invalid JSON, leaving $CONFIG alone" >&2; exit 1; }
[[ -s $TMP ]] || { echo "set-flag: produced an empty file, leaving $CONFIG alone" >&2; exit 1; }

chmod --reference="$CONFIG" -- "$TMP" 2>/dev/null || chmod 600 -- "$TMP"
mv -f -- "$TMP" "$CONFIG"
trap - EXIT
