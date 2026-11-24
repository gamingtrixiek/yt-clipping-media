#!/data/data/com.termux/files/usr/bin/bash
# One-time setup: an always-on helper checks for work every 5 minutes by itself. Safe to run again.
pkg install -y util-linux procps >/dev/null 2>&1 || true      # flock, pgrep
RAW="https://api.github.com/repos/gamingtrixiek/yt-clipping-media/contents/scripts"
curl -fsSL -H "Accept: application/vnd.github.raw" "$RAW/auto_worker.sh" -o ~/auto_worker.sh
curl -fsSL -H "Accept: application/vnd.github.raw" "$RAW/yt_loop.sh" -o ~/yt_loop.sh
chmod +x ~/auto_worker.sh ~/yt_loop.sh

# the old 30-minute timer kept getting stopped by Android - remove it
( crontab -l 2>/dev/null | grep -v -e auto_worker -e '^PATH=' -e '^SHELL=' ) | crontab - 2>/dev/null || true
sed -i '/pgrep -x crond/d' ~/.bashrc 2>/dev/null || true

# start the helper whenever Termux opens
START='pgrep -f yt_loop.sh >/dev/null || (nohup bash ~/yt_loop.sh >/dev/null 2>&1 &)'
grep -q "yt_loop.sh" ~/.bashrc 2>/dev/null || echo "$START" >> ~/.bashrc
# and after a restart, if the free Termux:Boot app is installed
mkdir -p ~/.termux/boot
printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nnohup bash ~/yt_loop.sh >/dev/null 2>&1 &\n' > ~/.termux/boot/start-helper
chmod +x ~/.termux/boot/start-helper

termux-wake-lock 2>/dev/null || true
pgrep -f yt_loop.sh >/dev/null || (nohup bash ~/yt_loop.sh >/dev/null 2>&1 &)
sleep 3
if pgrep -f yt_loop.sh >/dev/null; then
  echo "✅ Helper is ON - it checks for jobs every 5 minutes by itself."
  echo "   You never need to run anything again. Just don't swipe Termux away."
  echo "   (Progress log: tail ~/auto.log)"
else
  echo "❌ Helper didn't start - send Claude a screenshot."
fi
