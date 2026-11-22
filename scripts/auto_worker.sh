#!/data/data/com.termux/files/usr/bin/bash
# Runs by itself every 30 min on the tablet. Picks up jobs Claude leaves in the private repo's
# queue/ folder (scan a new stream, or download clip windows), runs them, then removes the job.
# Nothing to type. Downloads are deleted from the tablet right after upload.
PRIV="gamingtrixiek/Yt-clipping"
PUB="gamingtrixiek/yt-clipping-media"
exec 9>"$HOME/.auto_worker.lock"; flock -n 9 || exit 0          # never two at once
notify() { command -v termux-notification >/dev/null && termux-notification --title "PS5eXtreme helper" --content "$1" || true; }
cd "$HOME"
curl -fsSL -H "Accept: application/vnd.github.raw" \
  "https://api.github.com/repos/$PUB/contents/scripts/get_clips.sh" -o "$HOME/get_clips.sh" || exit 0
gh api "repos/$PRIV/contents/queue" --jq '.[] | select(.name|endswith(".json")) | "\(.name) \(.sha)"' 2>/dev/null |
while read -r NAME SHA; do
  JOB=$(gh api "repos/$PRIV/contents/queue/$NAME" -H "Accept: application/vnd.github.raw") || continue
  TYPE=$(echo "$JOB" | python3 -c 'import json,sys;print(json.load(sys.stdin)["type"])')
  echo "$(date '+%F %T') job $NAME ($TYPE)"
  notify "Working on $NAME…"
  if [ "$TYPE" = "scan" ]; then
    IDS=$(echo "$JOB" | python3 -c 'import json,sys;print(" ".join(json.load(sys.stdin)["ids"]))')
    bash "$HOME/get_clips.sh" --scan $IDS < /dev/null
  else
    TAG=$(echo "$JOB" | python3 -c 'import json,sys;print(json.load(sys.stdin)["tag"])')
    bash "$HOME/get_clips.sh" "$TAG" < /dev/null
  fi && {
    gh api -X DELETE "repos/$PRIV/contents/queue/$NAME" -f message="done $NAME" -f sha="$SHA" >/dev/null
    notify "✅ Done: $NAME"
  } || notify "⚠️ $NAME failed - will retry in 30 min"
done
rm -rf "$HOME/clips" "$HOME/.cache/yt-dlp" 2>/dev/null
exit 0
