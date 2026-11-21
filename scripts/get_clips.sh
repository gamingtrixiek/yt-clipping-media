#!/data/data/com.termux/files/usr/bin/bash
# Runs on the tablet (Termux). Downloads ONLY the chosen moments from the stream VOD over
# your home connection (YouTube blocks GitHub's servers), hands them to the Shorts maker,
# then deletes them from the tablet.
#
#   Test:  bash get_clips.sh --check
#   Real:  bash get_clips.sh <tag>        (Claude tells you the tag)
set -euo pipefail
REPO="gamingtrixiek/yt-clipping-media"
RAW="https://raw.githubusercontent.com/$REPO/main"
PAD_BEFORE=3
PAD_AFTER=2
YTDLP=(yt-dlp --js-runtimes node -f "bv*[height<=1080][fps<=60]+ba/b" --merge-output-format mp4 --no-warnings --no-playlist)

if [ "${1:-}" = "--check" ]; then
  echo "Testing a 5-second download..."
  "${YTDLP[@]}" --download-sections "*60-65" -o "$HOME/check.mp4" "https://www.youtube.com/watch?v=${2:-jNQXAC9IVRw}" < /dev/null
  [ -s "$HOME/check.mp4" ] && echo "✅ Downloads work on this tablet." && rm -f "$HOME/check.mp4"
  gh auth status >/dev/null 2>&1 && echo "✅ GitHub login OK." || echo "❌ Run: gh auth login"
  exit 0
fi

if [ "${1:-}" = "--scan" ]; then
  shift
  [ $# -gt 0 ] || { echo "Usage: bash get_clips.sh --scan <video_id> [more ids...]"; exit 1; }
  for VID in "$@"; do
    TAG="scan-$VID"
    WORK="$HOME/clips/$TAG"; mkdir -p "$WORK" && cd "$WORK"
    echo "=== $VID: downloading a small low-quality copy (only used to find highlights) ==="
    [ -s scan.mp4 ] || yt-dlp --js-runtimes node --no-warnings --no-playlist \
        -f "bv*[height<=160]+wa/wa" --merge-output-format mp4 -o scan.mp4 \
        "https://www.youtube.com/watch?v=$VID" < /dev/null
    ls -la scan.mp4
    echo "Uploading (temporary, auto-deleted)..."
    gh release create "$TAG" scan.mp4 -R "$REPO" --title "$TAG (temporary)" \
      --notes "Temporary. Deleted automatically." --latest=false \
      || gh release upload "$TAG" scan.mp4 -R "$REPO" --clobber
    gh workflow run scan.yml -R "$REPO" -f source_tag="$TAG"
    cd "$HOME" && rm -rf "$WORK"
    echo "✅ $VID uploaded"
  done
  echo "✅ All done! Tell Claude: scans uploaded"
  exit 0
fi

TAG="${1:?Usage: bash get_clips.sh <tag>}"
WORK="$HOME/clips/$TAG"
mkdir -p "$WORK" && cd "$WORK"
curl -fsSL -H "Accept: application/vnd.github.raw" -o plan.json "https://api.github.com/repos/$REPO/contents/plans/$TAG.json"

python - > list.txt <<'PY'
import json
def sec(t):
    x = 0.0
    for part in str(t).split(":"):
        x = x * 60 + float(part)
    return x
p = json.load(open("plan.json"))
for c in p["clips"]:
    print(c["id"], sec(c["start"]), sec(c["end"]), c.get("video_id", p.get("video_id")))
PY
N=$(wc -l < list.txt)
echo "Downloading $N moments..."

i=0
while read -r id st en VID; do
  i=$((i + 1))
  a=$(python -c "print(max(0, $st - $PAD_BEFORE))")
  b=$(python -c "print($en + $PAD_AFTER)")
  f=$(printf "raw_%02d.mp4" "$id")
  echo "[$i/$N] clip $id"
  if [ ! -s "$f" ]; then
    "${YTDLP[@]}" --download-sections "*$a-$b" -o "$f" "https://www.youtube.com/watch?v=$VID" < /dev/null \
      || { echo "⚠️ clip $id failed, retrying once..."; rm -f "$f"*; sleep 3; \
           "${YTDLP[@]}" --download-sections "*$a-$b" -o "$f" "https://www.youtube.com/watch?v=$VID" < /dev/null; } \
      || { echo "⚠️ skipping clip $id"; rm -f "$f"*; }
  fi
done < list.txt

echo "Uploading to the Shorts maker (temporary, auto-deleted)..."
ls raw_*.mp4 >/dev/null 2>&1 || { echo "❌ No clips downloaded"; exit 1; }
gh release create "$TAG-raw" raw_*.mp4 -R "$REPO" --title "$TAG raw (temporary)" \
  --notes "Temporary files. Deleted automatically." --latest=false \
  || gh release upload "$TAG-raw" raw_*.mp4 -R "$REPO" --clobber

PLAN=$(python - "$TAG" "$REPO" "$PAD_BEFORE" <<'PY'
import json, sys
tag, repo, pad = sys.argv[1], sys.argv[2], float(sys.argv[3])
def sec(t):
    x = 0.0
    for part in str(t).split(":"):
        x = x * 60 + float(part)
    return x
p = json.load(open("plan.json"))
for c in p["clips"]:
    c["source_url"] = f"https://github.com/{repo}/releases/download/{tag}-raw/raw_{int(c['id']):02d}.mp4"
    c["source_start"] = min(pad, sec(c["start"]))
print(json.dumps(p, ensure_ascii=False))
PY
)
gh workflow run make-shorts.yml -R "$REPO" -f tag="$TAG" -f plan="$PLAN"
cd "$HOME" && rm -rf "$WORK"
echo "✅ Done! The Shorts are being made now. Tell Claude: clips uploaded"
