#!/data/data/com.termux/files/usr/bin/bash
# Always-on helper: every 5 minutes, fetch the latest worker and run any queued jobs.
# Started by setup_auto.sh, and again automatically whenever Termux opens (~/.bashrc)
# or the tablet restarts (Termux:Boot, if installed). Only one copy ever runs.
export PATH="/data/data/com.termux/files/usr/bin:$PATH"
exec 8>"$HOME/.yt_loop.lock"; flock -n 8 || exit 0
termux-wake-lock 2>/dev/null || true
PRIV="gamingtrixiek/Yt-clipping"
while true; do
  curl -fsSL -H "Accept: application/vnd.github.raw" \
    https://api.github.com/repos/gamingtrixiek/yt-clipping-media/contents/scripts/auto_worker.sh \
    -o "$HOME/auto_worker.sh.new" && mv "$HOME/auto_worker.sh.new" "$HOME/auto_worker.sh"
  bash "$HOME/auto_worker.sh" >> "$HOME/auto.log" 2>&1
  # heartbeat so Claude can see the helper is alive (a repo variable, no commits)
  NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  gh api -X PATCH "repos/$PRIV/actions/variables/TABLET_SEEN" -f name=TABLET_SEEN -f value="$NOW" >/dev/null 2>&1 \
    || gh api -X POST "repos/$PRIV/actions/variables" -f name=TABLET_SEEN -f value="$NOW" >/dev/null 2>&1
  tail -c 200000 "$HOME/auto.log" > "$HOME/auto.log.tmp" 2>/dev/null && mv "$HOME/auto.log.tmp" "$HOME/auto.log"
  sleep 300
done
