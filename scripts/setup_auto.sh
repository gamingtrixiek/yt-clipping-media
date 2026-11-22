#!/data/data/com.termux/files/usr/bin/bash
# One-time setup: the tablet checks for work every 30 minutes by itself. Safe to run again.
set -e
if ! pkg install -y cronie termux-services util-linux; then
  echo "❌ The Termux download server is busy. Run:  termux-change-repo"
  echo "   pick 'Single mirror', then any mirror (e.g. the first one), OK - then run this command again."
  exit 1
fi
curl -fsSL -H "Accept: application/vnd.github.raw" \
  https://api.github.com/repos/gamingtrixiek/yt-clipping-media/contents/scripts/auto_worker.sh -o ~/auto_worker.sh
( crontab -l 2>/dev/null | grep -v auto_worker ; \
  echo '*/30 * * * * curl -fsSL -H "Accept: application/vnd.github.raw" https://api.github.com/repos/gamingtrixiek/yt-clipping-media/contents/scripts/auto_worker.sh -o ~/auto_worker.sh; bash ~/auto_worker.sh >> ~/auto.log 2>&1' ) | crontab -
termux-wake-lock 2>/dev/null || true
if sv-enable crond 2>/dev/null; then
  echo "✅ Automatic helper is ON. Leave Termux running in the background (you can close the screen)."
else
  echo "⏳ Almost done: fully close Termux (swipe it away), open it again, and run this same command once more."
fi
