#!/usr/bin/env python3
"""Keep nudity and vulgar language out of everything we publish.

  nsfw_ranges(video, every)  -> [(start, end, label, score)]  exposed-nudity moments (NudeNet)
  is_clean(video)            -> (True/False, ranges)           strict gate used before a Short is finished
  mute_profanity(src, dst)   -> number of words muted          silences swear words in the game audio

Command line (used by the scan workflow on the whole stream):
  python3 scripts/safety.py scan <video> <out.json> [every_seconds]
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NUDE = ("FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
        "BUTTOCKS_EXPOSED", "ANUS_EXPOSED")
SWEAR = re.compile(r"^(f+u+c*k+\w*|motherf\w*|sh+i+t+\w*|bullshit|bitch\w*|cunt\w*|dick(s|head\w*)?|cock(s|sucker\w*)?|puss(y|ies)"
                   r"|ass(hole)?s?|bastard\w*|whore\w*|slut\w*|nigg\w*|fag\w*|twat\w*|wank\w*|damn\w*|hell)$")

_det = None


def _detector():
    global _det
    if _det is None:
        from nudenet import NudeDetector
        _det = NudeDetector()
    return _det


def _dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def nsfw_ranges(video, every=1.0, width=640, threshold=0.45, pad=None):
    """Sample one frame every `every` seconds and return merged time ranges showing nudity."""
    pad = every if pad is None else pad
    hits = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                        f"fps=1/{every},scale={width}:-2:flags=lanczos", "-q:v", "3", f"{d}/f%06d.jpg"], check=True)
        det = _detector()
        for f in sorted(Path(d).glob("f*.jpg")):
            t = (int(f.stem[1:]) - 1) * every
            found = [x for x in det.detect(str(f)) if x["class"] in NUDE and x["score"] >= threshold]
            if found:
                best = max(found, key=lambda x: x["score"])
                hits.append((t, best["class"], round(float(best["score"]), 2)))
    ranges = []
    for t, label, score in hits:
        s, e = max(0.0, t - pad), t + pad
        if ranges and s <= ranges[-1][1]:
            r = ranges[-1]
            ranges[-1] = (r[0], e, r[2] if r[3] >= score else label, max(r[3], score))
        else:
            ranges.append((s, e, label, score))
    return ranges


def is_clean(video):
    """Strict check for a finished-quality clip: 2 frames per second, lower threshold."""
    r = nsfw_ranges(video, every=0.5, width=960, threshold=0.35)
    return (not r), r


def mute_profanity(src, dst, language="en"):
    """Silence swear words in the game audio. Returns how many were muted (0 = file copied unchanged)."""
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(src), language=language, word_timestamps=True, vad_filter=True)
    bad = []
    for s in segs:
        for w in s.words or []:
            word = re.sub(r"[^a-z]", "", w.word.lower())
            if word and SWEAR.match(word):
                bad.append((max(0.0, w.start - 0.15), w.end + 0.15, word))
    if not bad:
        subprocess.run(["cp", str(src), str(dst)], check=True)
        return 0, []
    cond = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b, _ in bad)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "copy", "-af",
                    f"volume=enable='{cond}':volume=0", "-c:a", "aac", "-b:a", "192k", str(dst)], check=True)
    return len(bad), [w for _, _, w in bad]


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "scan":
        every = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
        rs = nsfw_ranges(sys.argv[2], every=every, threshold=0.40)
        out = [{"start": hms(s), "end": hms(e), "start_s": round(s, 1), "end_s": round(e, 1),
                "label": lab, "score": sc} for s, e, lab, sc in rs]
        Path(sys.argv[3]).write_text(json.dumps(out, indent=1))
        print(f"{len(out)} flagged range(s) in {hms(_dur(sys.argv[2]))}")
        for o in out:
            print(f"  {o['start']} - {o['end']}  {o['label']} ({o['score']})")
    else:
        print(__doc__)
