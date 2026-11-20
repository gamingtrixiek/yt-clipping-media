#!/usr/bin/env python3
"""
Find highlight candidates in a long, no-commentary gameplay VOD without relying on chat.

Input: a low-resolution copy of the stream (144p-240p is plenty).
Signals, per second:
  - loudness jump: momentary loudness well above the surrounding 2 minutes
    (crashes, gunfire, sirens, explosions)
  - motion: how much the picture changes (chases, crashes, fast driving)
Output (build/scan/):
  candidates.json   top N windows, spaced apart, with scores
  cand_XX.jpg       a 2x2 contact sheet of each candidate for visual review
"""

import json
import math
import re
import subprocess
import sys
from pathlib import Path
from statistics import median

TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 24
MIN_GAP = 180        # seconds between candidates
CLIP_LEN = 50        # default clip length around a peak
OUT = Path("build/scan")


def run(cmd):
    print("+", " ".join(map(str, cmd))[:300], flush=True)
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


def loudness_per_second(path):
    """Momentary loudness (LUFS) sampled every second."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af",
                        "aresample=16000,ebur128=framelog=info", "-f", "null", "-"],
                       capture_output=True, text=True)
    per = {}
    for m in re.finditer(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+|-inf)", r.stderr):
        t, M = float(m.group(1)), m.group(2)
        val = -70.0 if M == "-inf" else max(-70.0, float(M))
        s = int(t)
        per[s] = max(per.get(s, -70.0), val)
    return per


def motion_per_second(path):
    """Mean absolute frame difference per second at tiny resolution."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-an", "-vf",
                        "fps=4,scale=64:36,format=gray,tblend=all_mode=difference,"
                        "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                        "-f", "null", "-"], capture_output=True, text=True)
    per, t = {}, None
    for line in r.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"YAVG=([\d.]+)", line)
        if m and t is not None:
            s = int(t)
            per.setdefault(s, []).append(float(m.group(1)))
    return {s: sum(v) / len(v) for s, v in per.items()}


def zscores(series, n, window=120):
    vals = [series.get(i, None) for i in range(n)]
    fill = median([v for v in vals if v is not None] or [0])
    vals = [fill if v is None else v for v in vals]
    out = []
    for i in range(n):
        lo, hi = max(0, i - window), min(n, i + window)
        ctx = vals[lo:hi]
        med = median(ctx)
        mad = median([abs(x - med) for x in ctx]) or 1.0
        out.append((vals[i] - med) / (1.4826 * mad))
    return out


def hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main(path):
    OUT.mkdir(parents=True, exist_ok=True)
    dur = duration(path)
    n = int(dur)
    print(f"Duration {hms(dur)}; analysing audio...", flush=True)
    loud = loudness_per_second(path)
    print("Analysing motion...", flush=True)
    mot = motion_per_second(path)
    zl, zm = zscores(loud, n), zscores(mot, n)

    # smooth over 8 seconds, combine; skip first/last 2 minutes (menus, start/end screens)
    raw = [max(0.0, zl[i]) * 0.6 + max(0.0, zm[i]) * 0.4 for i in range(n)]
    score = []
    for i in range(n):
        w = raw[max(0, i - 4):i + 4]
        score.append(sum(w) / len(w))
    for i in list(range(0, min(n, 120))) + list(range(max(0, n - 120), n)):
        score[i] = 0.0

    order = sorted(range(n), key=lambda i: score[i], reverse=True)
    picked = []
    for i in order:
        if score[i] <= 0 or len(picked) >= TOP_N:
            break
        if all(abs(i - p) >= MIN_GAP for p in picked):
            picked.append(i)
    picked.sort()

    cands = []
    for k, peak in enumerate(picked, 1):
        start = max(0, peak - 30)
        end = min(n, start + CLIP_LEN)
        sheet = OUT / f"cand_{k:02d}.jpg"
        times = [start + (end - start) * f for f in (0.15, 0.4, 0.6, 0.85)]
        inputs = []
        for t in times:
            inputs += ["-ss", f"{t:.1f}", "-i", str(path)]
        filt = "".join(f"[{j}:v]scale=480:270,setsar=1,trim=end_frame=1[v{j}];" for j in range(4))
        filt += "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0," \
                f"drawtext=text='#{k} {hms(start).replace(':', '\\:')}':x=10:y=10:fontsize=34:fontcolor=yellow:box=1:boxcolor=black@0.6"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", filt,
                            "-frames:v", "1", "-q:v", "4", str(sheet)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"contact sheet {k} failed: {e}", flush=True)
        cands.append({"id": k, "peak": hms(peak), "start": hms(start), "end": hms(end),
                      "start_sec": start, "end_sec": end, "score": round(score[peak], 2),
                      "loudness_z": round(zl[peak], 1), "motion_z": round(zm[peak], 1),
                      "sheet": sheet.name})
    (OUT / "candidates.json").write_text(json.dumps({"duration": hms(dur), "candidates": cands}, indent=2))
    print(f"{len(cands)} candidates written", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
