#!/usr/bin/env python3
"""
Turn stream moments into finished YouTube Shorts.

For each clip in a plan file (plans/<name>.json) this script:
  1. Cuts the exact moment straight from the YouTube VOD (yt-dlp + ffmpeg, no full download)
  2. Transcribes the speech with faster-whisper (word timestamps)
  3. Renders a 1080x1920 vertical video: blurred background + zoomed gameplay,
     high-quality Lanczos upscale + sharpening, 60 fps kept, loudness normalised
  4. Burns in bold word-by-word captions (current word highlighted) and an optional hook line
  5. Saves thumbnail candidates and small preview frames

Plan file example:
{
  "name": "gta6-launch",
  "video_id": "abcdEFGhij",
  "language": null,                 # null = auto-detect, or "en" / "hi"
  "default_layout": "square",       # fit | square | tall | full
  "clips": [
    {"id": 1, "start": "1:02:30", "end": "1:03:35", "hook": "COPS WENT CRAZY"}
  ]
}
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

W, H = 1080, 1920
FONT_NAME = "Anton"
LAYOUTS = {             # foreground crop aspect (width / height) taken from the 16:9 source
    "fit": 16 / 9,      # whole frame, small, lots of blur
    "square": 1.0,      # good default for gameplay: big and still shows the action
    "tall": 4 / 5,      # bigger, crops more of the sides
    "full": 9 / 16,     # fills the screen, crops the most
}


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd)[:400], flush=True)
    return subprocess.run(cmd, check=True, **kw)


def to_sec(t):
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(p) for p in str(t).split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


# ---------------------------------------------------------------- source
def get_source(video_id, start, dur, workdir, source_url=None):
    """Cut [start, start+dur] from the VOD into a near-lossless intermediate file.

    source_url: a direct MP4 link to the full VOD (e.g. imported through vidIQ). Preferred,
    because YouTube sometimes blocks downloads from GitHub's servers."""
    out = workdir / "source.mkv"
    if source_url:
        for attempt in range(3):
            try:
                run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                     "-i", source_url, "-map", "0:v:0", "-map", "0:a:0?",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
                     "-c:a", "pcm_s16le", str(out)])
                return out
            except Exception as e:
                print(f"Cut from source_url failed (try {attempt + 1}): {e}", flush=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    fmt = "bv*[height<=1080][fps<=60][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b"
    try:
        info = json.loads(subprocess.run(
            ["yt-dlp", "-f", fmt, "-j", "--no-warnings", url],
            check=True, capture_output=True, text=True).stdout)
        fmts = info.get("requested_formats") or [info]
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for f in fmts:
            hdrs = "".join(f"{k}: {v}\r\n" for k, v in (f.get("http_headers") or {}).items())
            cmd += ["-headers", hdrs, "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", f["url"]]
        maps = ["-map", "0:v:0", "-map", f"{len(fmts) - 1}:a:0"]
        run(cmd + maps + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
                          "-c:a", "pcm_s16le", str(out)])
        return out
    except Exception as e:  # fall back to yt-dlp's own section downloader
        print(f"Direct cut failed ({e}); falling back to yt-dlp sections", flush=True)
        end = start + dur
        run(["yt-dlp", "-f", fmt, "--download-sections", f"*{start:.2f}-{end:.2f}",
             "--force-keyframes-at-cuts", "--merge-output-format", "mkv",
             "-o", str(workdir / "source.%(ext)s"), url])
        return next(workdir.glob("source.*"))


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate", "-of", "json", str(path)],
                       check=True, capture_output=True, text=True)
    s = json.loads(r.stdout)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return s["width"], s["height"], float(num) / float(den)


# ---------------------------------------------------------------- captions
_model = None


def transcribe(path, language):
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"),
                              device="cpu", compute_type="int8")
    import numpy as np  # decode with our ffmpeg (avoids PyAV version clashes)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segs, info = _model.transcribe(audio, language=language or None,
                                   word_timestamps=True, vad_filter=True)
    words = []
    for seg in segs:
        for w in seg.words or []:
            txt = w.word.strip()
            if txt:
                words.append({"start": w.start, "end": w.end, "word": txt})
    return words, info.language


def build_ass(words, hook, fg_bottom, dur):
    cap_y = min(H - 330, fg_bottom + 150)          # baseline of captions
    margin_v = H - cap_y
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{FONT_NAME},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        f"0,0,0,0,100,100,1,0,1,7,3,2,60,60,{margin_v},1",
        f"Style: Hook,{FONT_NAME},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,"
        "0,0,0,0,100,100,1,0,3,18,0,8,70,70,190,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(dur)},Hook,,0,0,0,,"
                     f"{{\\fad(150,0)}}{ass_escape(hook.upper())}")

    # groups of up to 3 words, broken on long pauses
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["start"] - cur[-1]["end"] > 0.6):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    for gi, g in enumerate(groups):
        next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        for i, w in enumerate(g):
            start = w["start"]
            end = g[i + 1]["start"] if i + 1 < len(g) else max(w["end"], start + 0.25)
            if next_start is not None:      # never overlap the next caption line
                end = min(end, next_start)
            if end <= start:
                continue
            parts = []
            for j, x in enumerate(g):
                t = ass_escape(x["word"].upper())
                parts.append("{\\c&H00E6FF&\\fscx110\\fscy110}" + t + "{\\r}" if j == i else t)
            pop = "{\\t(0,80,\\fscx104\\fscy104)\\t(80,160,\\fscx100\\fscy100)}" if i == 0 else ""
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,"
                         + pop + " ".join(parts))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- render
def render(src, ass_path, layout, out, fps):
    aspect = LAYOUTS.get(layout, 1.0)
    fg_w = W
    fg_h = min(H, int(round(W / aspect / 2) * 2))
    fg_y = max(0, (H - fg_h) // 2 - (80 if layout != "full" else 0))
    fps = min(60, round(fps)) or 30
    crop = f"crop='min(iw,ih*{aspect:.5f})':'min(ih,iw/{aspect:.5f})'"
    vf = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic,crop={W}:{H},"
        f"boxblur=30:3,eq=brightness=-0.10:saturation=1.25[bg];"
        f"[b]{crop},scale={fg_w}:{fg_h}:flags=lanczos,"
        f"unsharp=5:5:0.7:5:5:0.0,eq=contrast=1.04:saturation=1.10[fg];"
        f"[bg][fg]overlay=(W-w)/2:{fg_y},fps={fps},"
        f"subtitles='{ass_path}':fontsdir='{FONTS_DIR}',format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-loglevel", "error", "-stats", "-i", str(src),
         "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0?",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high",
         "-maxrate", "20M", "-bufsize", "40M", "-g", str(fps * 2),
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(out)])
    return fg_y + fg_h


def frames(video, out_dir, stem, dur, n=6):
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        full = out_dir / f"{stem}_frame{k + 1}.jpg"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(full)])
        made.append({"t": round(t, 2), "file": full.name})
    return made


def esc(t):
    return t.replace("\\", "").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def make_thumbnail(src, at, text, badge, out):
    """1280x720 thumbnail from our own 1080p gameplay frame + bold text (no logos, no faces added)."""
    font = os.path.join(FONTS_DIR, "Anton-Regular.ttf")
    lines = [l.strip().upper() for l in text.split("|") if l.strip()][:2]
    vf = ("scale=1280:720:flags=lanczos,eq=contrast=1.12:saturation=1.35:brightness=0.02,"
          "unsharp=5:5:0.8:5:5:0.0,vignette=PI/5")
    y = 40
    for i, line in enumerate(lines):
        color = "white" if i == 0 else "yellow"
        size = 150 if len(line) <= 10 else (118 if len(line) <= 16 else 92)
        vf += (f",drawtext=fontfile='{font}':text='{esc(line)}':fontsize={size}:fontcolor={color}"
               f":borderw=12:bordercolor=black:shadowx=6:shadowy=6:shadowcolor=black@0.6:x=48:y={y}")
        y += size + 10
    if badge:
        vf += (f",drawbox=x=iw-330:y=ih-130:w=290:h=90:color=red@0.95:t=fill,"
               f"drawtext=fontfile='{font}':text='{esc(badge.upper())}':fontsize=64:fontcolor=white"
               f":x=w-330+(290-tw)/2:y=h-130+(90-th)/2")
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
         "-vf", vf, "-q:v", "2", str(out)])


def main(plan_arg):
    if plan_arg.lstrip().startswith("{"):
        plan = json.loads(plan_arg)
    else:
        plan = json.loads(Path(plan_arg).read_text())
    name = plan["name"]
    out = Path("build") / name
    out.mkdir(parents=True, exist_ok=True)
    report = {"name": name, "video_id": plan.get("video_id"), "clips": []}

    for clip in plan["clips"]:
        cid = clip["id"]
        start, end = to_sec(clip["start"]), to_sec(clip["end"])
        dur = end - start
        if clip.get("kind") == "still":
            try:
                work = Path("work") / f"still_{cid:02d}"
                work.mkdir(parents=True, exist_ok=True)
                ss = to_sec(clip.get("source_start", 0))
                src = get_source(clip.get("video_id", plan.get("video_id")), ss, dur, work, clip.get("source_url"))
                thumb = out / f"thumb_{cid:02d}.jpg"
                make_thumbnail(src, min(1.0, dur / 2), clip.get("text", ""), clip.get("badge", ""), thumb)
                run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(thumb), "-vf", "scale=480:-2",
                     "-q:v", "4", str(out / f"thumb_{cid:02d}_prev.jpg")])
                report["clips"].append({"id": cid, "kind": "still", "file": thumb.name,
                                        "video_id": clip.get("video_id"), "text": clip.get("text")})
                print(f"Thumbnail {cid} done", flush=True)
            except Exception as e:
                print(f"Thumbnail {cid} FAILED: {e}", flush=True)
                report["clips"].append({"id": cid, "kind": "still", "error": str(e)})
            continue
        stem = f"short_{cid:02d}"
        if not 5 <= dur <= 180:  # (stills are handled above)
            print(f"Skipping clip {cid}: length {dur:.0f}s (Shorts must be up to 3 minutes)")
            continue
        work = Path("work") / stem
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        try:
            if clip.get("source_url"):     # pre-cut section (e.g. downloaded on the tablet)
                src_start = to_sec(clip.get("source_start", 0))
                src = get_source(clip.get("video_id", plan.get("video_id")), src_start, dur, work, clip["source_url"])
            else:
                src = get_source(plan["video_id"], start, dur, work, plan.get("source_url"))
            _, _, fps = probe(src)
            words, lang = transcribe(src, plan.get("language"))
            layout = clip.get("layout") or plan.get("default_layout", "square")
            aspect = LAYOUTS.get(layout, 1.0)
            fg_h = min(H, int(round(W / aspect / 2) * 2))
            fg_bottom = max(0, (H - fg_h) // 2 - (80 if layout != "full" else 0)) + fg_h
            ass = work / "captions.ass"
            ass.write_text(build_ass(words, clip.get("hook"), fg_bottom, dur), encoding="utf-8")
            video = out / f"{stem}.mp4"
            render(src, ass, layout, video, fps)
            fr = frames(video, out, stem, dur)
            for f in fr:   # small copies for quick review
                small = f["file"].replace(".jpg", "_prev.jpg")
                run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / f["file"]),
                     "-vf", "scale=360:-2", "-q:v", "5", str(out / small)])
            text = " ".join(w["word"] for w in words)
            report["clips"].append({
                "id": cid, "file": video.name, "video_id": clip.get("video_id", plan.get("video_id")), "start": clip["start"], "end": clip["end"],
                "seconds": round(dur, 1), "layout": layout, "hook": clip.get("hook"),
                "language": lang, "transcript": text, "frames": fr,
                "size_mb": round(video.stat().st_size / 1e6, 1),
            })
            print(f"Clip {cid} done: {video} ({report['clips'][-1]['size_mb']} MB)", flush=True)
        except Exception as e:
            print(f"Clip {cid} FAILED: {e}", flush=True)
            report["clips"].append({"id": cid, "error": str(e)})

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    ok = sum(1 for c in report["clips"] if "error" not in c)
    print(f"Finished: {ok}/{len(plan['clips'])} clips")
    if ok == 0:
        sys.exit(1)


FONTS_DIR = os.environ.get("FONTS_DIR", "fonts")

if __name__ == "__main__":
    main(sys.argv[1])
