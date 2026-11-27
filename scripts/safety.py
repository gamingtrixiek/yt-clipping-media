#!/usr/bin/env python3
"""Keep nudity and vulgar language out of everything we publish.

  nsfw_ranges(video, every)  -> [(start, end, label, score)]  exposed-nudity moments (NudeNet)
  is_clean(video)            -> (ok, hard, soft)               moderate gate: blocks explicit nudity only
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
                   r"|ass(hole)?s?|bastard\w*|whore\w*|slut\w*|nigg\w*|fag\w*|twat\w*|wank\w*|damn\w*|hell"
                   # sexual / vulgar terms (owner rule 25 Nov: no vulgar content)
                   r"|tits?|titt(y|ies)|boobs?|boob(y|ies)|nipples?|dildos?|horny|cum(ming|shot)?|jizz\w*|blowjobs?|handjobs?"
                   r"|porn\w*|sex|sexy|nudes?|naked|strippers?|hookers?|thots?|hoes?|pricks?|bollocks|piss\w*|douche\w*|skank\w*|milf\w*)$")

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


HARD_ALWAYS = ("FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED")


def frame_hits(video, every=0.5, width=960, threshold=0.35):
    """[(t, {class: best score})] for frames with any exposed-nudity detection."""
    out = []
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                        f"fps=1/{every},scale={width}:-2:flags=lanczos", "-q:v", "3", f"{d}/f%06d.jpg"], check=True)
        det = _detector()
        for f in sorted(Path(d).glob("f*.jpg")):
            t = (int(f.stem[1:]) - 1) * every
            found = {}
            for x in det.detect(str(f)):
                if x["class"] in NUDE and x["score"] >= threshold:
                    found[x["class"]] = max(found.get(x["class"], 0), round(float(x["score"]), 2))
            if found:
                out.append((t, found))
    return out


def is_clean(video, every=0.5):
    """Moderate gate (user, 25 Nov): block only clear nudity — exposed genitals, a fully naked body
    (breasts AND buttocks/genitals together), or exposed breasts held on screen for 1.5 s+.
    Swimwear-style or one-frame detections are soft flags: allowed, but reported for a visual check.
    Returns (ok, hard_reasons, soft_times)."""
    hits = frame_hits(video, every=every)
    hard, soft, run = [], [], 0
    for t, f in hits:
        reason = None
        if any(f.get(c, 0) >= 0.5 for c in HARD_ALWAYS):
            reason = "genitals"
        elif f.get("FEMALE_BREAST_EXPOSED", 0) >= 0.5 and f.get("BUTTOCKS_EXPOSED", 0) >= 0.5:
            reason = "fully naked"
        if f.get("FEMALE_BREAST_EXPOSED", 0) >= 0.6:
            run += 1
            if run >= 3 and not reason:
                reason = "breasts on screen"
        else:
            run = 0
        if reason:
            hard.append(f"{t:.1f}s {reason}")
        else:
            soft.append(round(t, 1))
    return (not hard), hard, soft


def mute_profanity(src, dst, language="en"):
    """Silence swear words in the game audio. Returns how many were muted (0 = file copied unchanged)."""
    sys.path.insert(0, str(Path(__file__).parent))
    import make_shorts                       # reuse its Whisper model and ffmpeg-based decoding
    words, _ = make_shorts.transcribe(src, language)
    bad = []
    for w in words:
        word = re.sub(r"[^a-z]", "", w["word"].lower())
        if word and SWEAR.match(word):
            bad.append((max(0.0, w["start"] - 0.15), w["end"] + 0.15, word))
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
