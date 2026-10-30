#!/bin/sh
set -eu
if [ "$#" -lt 1 ]; then
  echo 'Usage: ./download-macos.command "/path/PTCG Lab Preview.app" --product CSVM2cC --cache ./cache --output ./resources-1'
  exit 2
fi
PTCG_IMAGES_APP=$1
shift
PTCG_IMAGES_TOOL_DIR=$(CDPATH= cd -- "${0%/*}" && pwd)
PTCG_IMAGES_PYTHON="$PTCG_IMAGES_APP/Contents/Resources/backend/python/bin/python3"
if [ ! -x "$PTCG_IMAGES_PYTHON" ]; then
  echo 'Select the macOS .app directory containing the bundled Python runtime.' >&2
  exit 2
fi
exec "$PTCG_IMAGES_PYTHON" -I -B -X utf8 "$PTCG_IMAGES_TOOL_DIR/git_card_images.py" --app "$PTCG_IMAGES_APP" "$@"
