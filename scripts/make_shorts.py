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
  "default_layout": "reel",         # reel (default) | fit | square | tall | full
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

W, H = 1080, 1920          # layout / caption canvas
OUT_W, OUT_H = 2160, 3840  # delivered size (4K, as the owner asked)
FONT_NAME = "Anton"
LAYOUTS = {             # foreground crop aspect (width / height) taken from the 16:9 source
    "fit": 16 / 9,      # whole frame, small, lots of blur
    "square": 1.0,      # good default for gameplay: big and still shows the action
    "tall": 4 / 5,      # bigger, crops more of the sides
    "full": 9 / 16,     # fills the screen, crops the most
    "reel": 1080 / 1640,  # ~85% of the screen, thin strips only (reference-quality look, the default)
}
DEFAULT_LAYOUT = "reel"
SUB_CUT = 0.80          # in-game subtitles live below 80% of the frame height: crop them off, never blur
HUD_CUT = 0.08          # top HUD strip (compass / objective bar) is cropped off too


def subject_track(src, aspect, every=0.5):
    """Keyframes [(t, x_frac)] that keep the characters inside a narrow vertical crop.
    Uses the NudeNet body/face detector already installed for the safety check (game characters
    are detected fine); frames with nobody detected hold the last position. Smoothed and rate-limited
    so the virtual camera pans gently instead of jumping."""
    import tempfile
    try:
        import safety
        det = safety._detector()
    except Exception as e:
        print(f"Auto-framing unavailable ({e}); centring", flush=True)
        return [(0.0, 0.5)]
    pts = []
    with tempfile.TemporaryDirectory() as d:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
             f"fps=1/{every},scale=960:-2", "-q:v", "3", f"{d}/f%05d.jpg"])
        for f in sorted(Path(d).glob("f*.jpg")):
            t = (int(f.stem[1:]) - 1) * every
            wsum = xsum = 0.0
            for x in det.detect(str(f)):
                if x["score"] < 0.3:
                    continue
                bx, by, bw, bh = x["box"]
                wt = x["score"] * (2.0 if "FACE" in x["class"] else 1.0)
                if by > 540 * SUB_CUT:         # ignore anything in the cropped-off subtitle band
                    continue
                xsum += wt * (bx + bw / 2) / 960
                wsum += wt
            pts.append((t, xsum / wsum if wsum else None))
    xs, last = [], 0.5
    for t, c in pts:
        last = c if c is not None else last
        xs.append(last)
    sm = []
    for i in range(len(xs)):          # 3-second median: ignore one-frame mis-detections
        w = sorted(xs[max(0, i - 3):i + 4])
        sm.append(w[len(w) // 2])
    crop_w = aspect * (SUB_CUT - HUD_CUT) * 1080 / 1920     # crop width as a fraction of frame width
    out, prev = [], None
    for (t, _), c in zip(pts, sm):
        frac = min(1.0, max(0.0, (c - crop_w / 2) / max(1e-6, 1 - crop_w)))
        if prev is not None:          # pan at most ~35% of the travel per second
            frac = prev + max(-0.175, min(0.175, frac - prev))
        prev = frac
        out.append((round(t, 2), round(frac, 3)))
    return out or [(0.0, 0.5)]


def simplify_keys(keys, limit=24):
    """Drop keyframes that lie on the line between their neighbours, then cap the count
    (ffmpeg's expression parser can't take very deep nesting)."""
    if len(keys) <= 2:
        return keys
    out = [keys[0]]
    for i in range(1, len(keys) - 1):
        (t0, a), (t1, b), (t2, c) = out[-1], keys[i], keys[i + 1]
        interp = a + (c - a) * (t1 - t0) / (t2 - t0) if t2 > t0 else b
        if abs(interp - b) > 0.02:
            out.append(keys[i])
    out.append(keys[-1])
    while len(out) > limit:          # still too many: keep every other inner point
        out = [out[0]] + out[1:-1][1::2] + [out[-1]]
    return out


def focus_expr(keys):
    """Piecewise-linear ffmpeg expression of t through the (t, frac) keyframes."""
    keys = simplify_keys(keys)
    if len(keys) == 1:
        return f"{keys[0][1]}"
    e = f"{keys[-1][1]}"
    for (t0, a), (t1, b) in reversed(list(zip(keys, keys[1:]))):
        e = f"if(lt(t\\,{t1})\\,{a}+({b}-{a})*(t-{t0})/{t1 - t0:.3f}\\,{e})"
    return f"if(lt(t\\,{keys[0][0]})\\,{keys[0][1]}\\,{e})"


def fg_box(layout):
    """(top, height) of the gameplay window on the 1080x1920 canvas."""
    if layout == "reel":
        return 150, 1640
    aspect = LAYOUTS.get(layout, 1.0)
    fg_h = min(H, int(round(W / aspect / 2) * 2))
    return max(0, (H - fg_h) // 2 - (80 if layout != "full" else 0)), fg_h


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


def build_ass(words, hook, fg_bottom, dur, hook_margin=None):
    if hook_margin is None:
        hook_margin = 30 if fg_bottom >= 1700 else 190
    if hook:   # the caption font has no emoji: drop them (titles keep theirs)
        hook = " ".join("".join(ch for ch in hook if ord(ch) < 0x2000 or ch in "…‘’“”–—").split())
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
        f"0,0,0,0,100,100,1,0,3,18,0,8,70,70,{hook_margin},1",
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
_tts = None
_cbx = None


def _words(t):
    import re
    t = t.lower().replace("gta 6", "gta six").replace("gta vi", "gta six")
    return re.sub(r"[^a-z0-9 ]", " ", t).split()


def _wer(ref, hyp):
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[-1] / max(1, len(r))


def _kokoro(text, out_wav, voice="am_michael"):
    global _tts
    import numpy as np, soundfile as sf
    from kokoro import KPipeline
    if _tts is None:
        _tts = KPipeline(lang_code="a")
    audio = np.concatenate([a for _, _, a in _tts(text, voice=voice, speed=1.08)])
    sf.write(str(out_wav), audio, 24000)


def _chatterbox(text, out_wav, exaggeration=0.75):
    global _cbx
    import soundfile as sf
    from chatterbox.tts import ChatterboxTTS
    if _cbx is None:
        _cbx = ChatterboxTTS.from_pretrained(device="cpu")
    w = _cbx.generate(text, exaggeration=exaggeration, cfg_weight=0.35)
    sf.write(str(out_wav), w.squeeze().cpu().numpy(), _cbx.sr)


_qwen = None


def _qwen_say(text, out_wav, instruct):
    global _qwen
    import torch, numpy as np, soundfile as sf
    from qwen_tts import Qwen3TTSModel
    if _qwen is None:
        _qwen = Qwen3TTSModel.from_pretrained(os.environ.get("QWEN_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"),
                                              device_map="cpu", dtype=torch.float32)
    wavs, sr = _qwen.generate_custom_voice(text=text, language="English", speaker="Ryan", instruct=instruct)
    sf.write(str(out_wav), np.asarray(wavs[0], dtype=np.float32).squeeze(), sr)
    return out_wav


def make_voice(text, out_wav, voice="am_michael", engine="qwen",
               instruct="Excited, energetic young gamer reacting to an awesome moment"):
    """engine='qwen': Qwen3-TTS + Whisper check + self-learning respellings, Kokoro backup."""
    import soundfile as sf
    if engine == "qwen":
        sys.path.insert(0, str(Path(__file__).parent))
        from voice_learn import Learner
        learner = Learner()
        used, tries = learner.speak(
            text,
            generate=lambda t: _qwen_say(t, out_wav, instruct),
            listen=lambda w: " ".join(x["word"] for x in transcribe(w, "en")[0]),
            fallback=lambda t: _kokoro(t, out_wav, voice))
        return sf.info(str(out_wav)).duration, f"{used} (try {tries})"
    return _make_voice_cbx(text, out_wav, voice, engine)


def _make_voice_cbx(text, out_wav, voice="am_michael", engine="chatterbox"):
    """Expressive free TTS (Chatterbox, MIT) with a Whisper check; falls back to Kokoro.
    Returns (duration_seconds, engine_used)."""
    import soundfile as sf
    if engine == "chatterbox":
        for attempt, ex in enumerate((0.75, 0.6, 0.5)):
            try:
                _chatterbox(text, out_wav, ex)
                heard, _ = transcribe(out_wav, "en")
                said = " ".join(w["word"] for w in heard)
                if _wer(text, said) <= 0.1:
                    return sf.info(str(out_wav)).duration, "chatterbox"
                print(f"Voice check failed (try {attempt + 1}): heard '{said}'", flush=True)
            except Exception as e:
                print(f"Chatterbox error: {e}", flush=True)
    _kokoro(text, out_wav, voice)
    return sf.info(str(out_wav)).duration, "kokoro"


def add_voice(video, wav, delay, out):
    """Mix narration over the game sound; game audio ducks while the voice speaks."""
    d = int(delay * 1000)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav), "-filter_complex",
         f"[1:a]aresample=48000,volume=2.2,adelay={d}|{d},apad[v];"
         "[v]asplit=2[vm][vs];[0:a][vs]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[g];"
         "[g][vm]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(out)])


def render(src, ass_path, layout, out, fps, focus_x="auto", cut_subs=True):
    """Gameplay window straight at 4K: light clean-up of stream compression, Lanczos upscale,
    contrast-adaptive sharpening and a little punch; thin blurred strips above/below.
    In-game subtitles are cropped away (bottom 20% of the frame), never blurred over."""
    fg_y, fg_h = fg_box(layout)
    k = OUT_W // W
    FW, FH, FY = W * k, fg_h * k, fg_y * k
    aspect = W / fg_h
    fps = min(60, round(fps)) or 30
    top, keep = (HUD_CUT, SUB_CUT - HUD_CUT) if cut_subs else (0.0, 1.0)
    if focus_x == "auto":
        keys = subject_track(src, aspect) if layout in ("reel", "full", "tall") else [(0.0, 0.5)]
        print(f"Auto-framing keyframes: {keys[:12]}{' ...' if len(keys) > 12 else ''}", flush=True)
    else:
        keys = [(0.0, float(focus_x))]
    fx = focus_expr(keys)
    # crop: band between the HUD and the subtitles, then the window's aspect, panned to the characters
    crop = (f"crop=iw:ih*{keep}:0:ih*{top},"
            f"crop='min(iw,ih*{aspect:.5f})':ih:'(iw-min(iw,ih*{aspect:.5f}))*({fx})':0")
    vf = (
        f"[0:v]split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
        f"gblur=sigma=12,eq=brightness=-0.16:saturation=1.2,scale={OUT_W}:{OUT_H}:flags=bicubic[bg];"
        f"[b]{crop},hqdn3d=1:1:3:3,scale={FW}:{FH}:flags=lanczos,"
        f"cas=0.75,eq=contrast=1.10:saturation=1.20:gamma=0.97,unsharp=3:3:0.4:3:3:0.0[fg];"
        f"[bg][fg]overlay=(W-w)/2:{FY},fps={fps},"
        f"subtitles='{ass_path}':fontsdir='{FONTS_DIR}',format=yuv420p[v]"
    )
    enc = ["-map", "[v]", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "15", "-profile:v", "high",
           "-maxrate", "45M", "-bufsize", "90M", "-g", str(fps * 2),
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(out)]
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-stats", "-i", str(src), "-filter_complex", vf] + enc)
    except subprocess.CalledProcessError:
        if fx == "0.5":
            raise
        print("Panning crop failed; rendering with a centred crop instead", flush=True)
        run(["ffmpeg", "-y", "-loglevel", "error", "-stats", "-i", str(src),
             "-filter_complex", vf.replace(f"({fx})", "(0.5)")] + enc)
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
    if plan.get("mode") in ("grab", "commentary"):
        sys.path.insert(0, str(Path(__file__).parent))
        import commentary
        commentary.run(plan, out)
        return

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
        if plan.get("mode") == "review":       # just a frame strip (1 frame / 3 s) for choosing moments
            try:
                work = Path("work") / f"rev_{cid:02d}"
                work.mkdir(parents=True, exist_ok=True)
                src = get_source(clip.get("video_id", plan.get("video_id")), to_sec(clip.get("source_start", 0)),
                                 dur, work, clip.get("source_url"))
                sheet = out / f"review_{cid:02d}.jpg"
                run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
                     f"fps=1/3,scale=320:180,drawtext=text='{cid} %{{pts\\:hms}}':x=4:y=4:fontsize=18"
                     ":fontcolor=yellow:box=1:boxcolor=black@0.6,tile=6x5", "-frames:v", "1", "-q:v", "4", str(sheet)])
                report["clips"].append({"id": cid, "kind": "review", "file": sheet.name, "topic": clip.get("topic")})
            except Exception as e:
                report["clips"].append({"id": cid, "kind": "review", "error": str(e)})
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
            layout = clip.get("layout") or plan.get("default_layout", DEFAULT_LAYOUT)
            fg_bottom = sum(fg_box(layout))
            ass = work / "captions.ass"
            ass.write_text(build_ass(words, clip.get("hook"), fg_bottom, dur), encoding="utf-8")
            video = out / f"{stem}.mp4"
            if clip.get("voice"):
                tmp = work / "novoice.mp4"
                render(src, ass, layout, tmp, fps)
                wav = work / "voice.wav"
                vdur, used = make_voice(clip["voice"], wav, clip.get("voice_name", "am_michael"),
                                        plan.get("voice_engine", "qwen"),
                                        **({"instruct": plan["instruct"]} if plan.get("instruct") else {}))
                add_voice(tmp, wav, clip.get("voice_at", 0.4), video)
                print(f"Voice added ({used}, {vdur:.1f}s): {clip['voice']}", flush=True)
            else:
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
