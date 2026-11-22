#!/data/data/com.termux/files/usr/bin/bash
# One-time: lets the private yt-clipping repo update YOUR stream titles and thumbnails
# through the official YouTube API. Nothing is uploaded; secrets are stored encrypted in GitHub.
set -euo pipefail
REPO="gamingtrixiek/yt-clipping"
read -rp "Paste the OAuth Client ID: " CID
read -rp "Paste the OAuth Client secret: " CSEC
CID=$(echo "$CID" | tr -d '[:space:]'); CSEC=$(echo "$CSEC" | tr -d '[:space:]')
R=$(curl -sS -d "client_id=$CID" --data-urlencode "scope=https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.force-ssl" https://oauth2.googleapis.com/device/code)
if ! echo "$R" | grep -q device_code; then
  echo "❌ Google refused: $R"
  echo "Most likely the client type is not 'TVs and Limited Input devices', or it was created less than 5 minutes ago."
  exit 1
fi
DC=$(python -c "import json,sys;print(json.loads(sys.argv[1])['device_code'])" "$R")
UC=$(python -c "import json,sys;print(json.loads(sys.argv[1])['user_code'])" "$R")
URL=$(python -c "import json,sys;print(json.loads(sys.argv[1])['verification_url'])" "$R")
INT=$(python -c "import json,sys;print(json.loads(sys.argv[1]).get('interval',5))" "$R")
echo; echo "👉 Open $URL in Chrome, sign in with the Google account that owns PS5eXtreme,"
echo "   enter this code: $UC   and allow access."; echo "Waiting..."
while true; do
  sleep "$INT"
  T=$(curl -sS -d "client_id=$CID" -d "client_secret=$CSEC" -d "device_code=$DC" \
      -d "grant_type=urn:ietf:params:oauth:grant-type:device_code" https://oauth2.googleapis.com/token)
  RT=$(python -c "import json,sys;print(json.loads(sys.argv[1]).get('refresh_token',''))" "$T")
  ERR=$(python -c "import json,sys;print(json.loads(sys.argv[1]).get('error',''))" "$T")
  [ -n "$RT" ] && break
  [ "$ERR" = "authorization_pending" ] || [ "$ERR" = "slow_down" ] || { echo "❌ $T"; exit 1; }
done
printf %s "$CID"  | gh secret set YT_CLIENT_ID -R "$REPO"
printf %s "$CSEC" | gh secret set YT_CLIENT_SECRET -R "$REPO"
printf %s "$RT"   | gh secret set YT_REFRESH_TOKEN -R "$REPO"
echo "✅ Done! Tell Claude: youtube linked"
