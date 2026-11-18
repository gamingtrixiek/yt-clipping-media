# yt-clipping-media

Turns stream moments into finished YouTube Shorts. This repo is public only so vidIQ can
fetch finished files, so **nothing private is kept here**:

- No chat logs, viewer names or API keys (those stay in the private `yt-clipping` repo).
- Clips exist only as temporary releases. They are deleted as soon as vidIQ has imported them,
  and an hourly job deletes anything older than 6 hours. Workflow logs are deleted too.
- `plans/` holds only a video ID, timestamps and hook text, and is removed after use.

## Pipeline

1. `yt-clipping` logs chat spikes during the stream → `moments.md` (private).
2. Claude picks the best moments and writes `plans/<tag>.json`.
3. On the tablet (Termux): `bash get_clips.sh <tag>` downloads only those moments over a home
   connection (YouTube blocks GitHub's servers) and starts the Shorts maker.
4. **Make Shorts** workflow: 1080×1920 vertical, blurred background + zoomed gameplay,
   Lanczos upscale + sharpening, loudness normalised, word-by-word burned-in captions
   (Whisper), hook banner, thumbnail frames.
5. Claude reviews the frames (skips cutscenes and spoilers), writes the title, description and
   hashtags, and uploads each Short **as Private** through vidIQ with a thumbnail.
6. You review in YouTube Studio and switch to Public.

## One-time tablet setup (Termux, about 10 minutes)

1. Install **Termux** from F-Droid (https://f-droid.org/packages/com.termux/). The Play Store
   version is outdated.
2. Open Termux and paste:
   ```
   pkg update -y && pkg install -y python ffmpeg nodejs gh curl
   pip install -U "yt-dlp[default]"
   curl -fsSL -o ~/get_clips.sh https://raw.githubusercontent.com/gamingtrixiek/yt-clipping-media/main/scripts/get_clips.sh
   gh auth login
   ```
   For `gh auth login` choose: GitHub.com → HTTPS → Yes → Login with a web browser,
   then copy the code and open the link.
3. Test: `bash ~/get_clips.sh --check` should print two ✅ lines.
