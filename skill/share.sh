#!/bin/sh
# {{MARKER}}: written on every dashboard start, so edits here are overwritten.
# Share an image with the user on the AI dashboard, or list the images they uploaded.
#
#   share.sh <image> ["caption"]   copy the image to the dashboard (this tmux session's card + Screenshots)
#   share.sh --inbox [n]           the newest n images the user uploaded (default 5)
#
# Each image gets a sidecar <id>.json (session, folder, caption, time). The sidecar is written
# first and the image is moved into place last, so the dashboard never sees a half-written one.
set -eu
DIR='{{SHOTS_DIR}}'
URL='{{URL}}'

field() {  # a string value from a sidecar (one key per line, as written below and by the dashboard)
  sed -n "s/^ *\"$2\": \"\(.*\)\",\{0,1\}\$/\1/p" "$1" | head -n 1
}

if [ "${1:-}" = "--inbox" ]; then
  n=${2:-5}
  found=0
  for img in $(ls -t "$DIR"/uploads/*.png "$DIR"/uploads/*.jpg "$DIR"/uploads/*.webp "$DIR"/uploads/*.gif 2>/dev/null | head -n "$n"); do
    meta="${img%.*}.json"
    when=$(date -r "$img" '+%Y-%m-%d %H:%M')
    sess=""; cap=""
    if [ -f "$meta" ]; then sess=$(field "$meta" session); cap=$(field "$meta" caption); fi
    echo "$img  ($when${sess:+, sent to $sess})${cap:+  $cap}"
    found=1
  done
  [ "$found" = 1 ] || echo "No uploads in $DIR/uploads."
  exit 0
fi

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  sed -n '5,6p' "$0" | sed 's/^# *//'
  exit 2
fi

src=$1
caption=${2:-}
[ -f "$src" ] || { echo "share.sh: no such file: $src" >&2; exit 1; }
ext=$(printf '%s' "${src##*.}" | tr 'A-Z' 'a-z')
[ "$ext" = jpeg ] && ext=jpg
case "$ext" in
  png|jpg|webp|gif) ;;
  *) echo "share.sh: $src is not a .png/.jpg/.webp/.gif" >&2; exit 1 ;;
esac

json() { printf '%s' "$1" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g'; }

session=""; created=0
if [ -n "${TMUX:-}" ] && command -v tmux >/dev/null 2>&1; then
  info=$(tmux display-message -p ${TMUX_PANE:+-t "$TMUX_PANE"} '#{session_name}	#{session_created}' 2>/dev/null || true)
  session=$(printf '%s' "$info" | cut -f1)
  created=$(printf '%s' "$info" | cut -f2)
  case "$created" in ''|*[!0-9]*) created=0 ;; esac
fi

mkdir -p "$DIR/shared"
id="$(date +%Y%m%d-%H%M%S)-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
cat > "$DIR/shared/$id.json" <<EOF
{
  "id": "$id",
  "session": "$(json "$session")",
  "session_created": $created,
  "cwd": "$(json "$PWD")",
  "caption": "$(json "$caption")",
  "created": $(date +%s)
}
EOF
cp "$src" "$DIR/shared/.$id.$ext.tmp"
mv "$DIR/shared/.$id.$ext.tmp" "$DIR/shared/$id.$ext"
echo "Shared on the dashboard: $DIR/shared/$id.$ext"
echo "The user sees it at $URL#shots${session:+ and on the card of session $session}."
