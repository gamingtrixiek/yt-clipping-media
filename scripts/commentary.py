"""Add AI co-commentary + an ending question to finished Shorts.

mode "grab":       download each clip (url) and make a 1-frame-per-second strip, so lines can be written.
mode "commentary": voice each line (Qwen + Whisper check + learned respellings), mix it in with the
                   game sound ducked, and show the ending question on screen for the last seconds."""
import json, shutil, subprocess, urllib.request
from pathlib import Path
import make_shorts as ms


def fetch(url, dst):
    urllib.request.urlretrieve(url, dst)
    return dst


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def question_ass(question, start, dur, path):
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {ms.W}", f"PlayResY: {ms.H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Q,{ms.FONT_NAME},84,&H0000E5FF,&H0000E5FF,&H00000000,&HD0000000,0,0,0,0,100,100,1,0,3,20,0,5,80,80,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    q = " ".join("".join(ch for ch in question if ord(ch) < 0x2000 or ch in "’…").split()).upper()
    lines.append(f"Dialogue: 2,{ms.ass_time(start)},{ms.ass_time(dur)},Q,,0,0,0,,"
                 f"{{\\fad(200,0)\\pos(540,1180)}}{ms.ass_escape(q)}\\N{{\\fs60}}COMMENT BELOW")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def narration_words(used):
    """Word timings for burned-in narration captions: spread each line's audio length over its words."""
    import re
    words = []
    for u in used:
        toks = [w for w in u["text"].split() if w.strip()]
        if not toks or not u.get("len"):
            continue
        total = sum(len(w) + 1 for w in toks)
        t = float(u["t"])
        for w in toks:
            d = u["len"] * (len(w) + 1) / total
            words.append({"word": re.sub(r"[^\w'?!.,-]", "", w) or w, "start": round(t, 2), "end": round(t + d, 2)})
            t += d
    return words


def game_words(video, busy, language="en"):
    """Captions for what the characters say (swears masked), skipping moments our narration is talking."""
    import safety
    words, _ = ms.transcribe(video, language)
    out = []
    for w in words:
        if any(a - 0.2 <= w["start"] <= b + 0.2 for a, b in busy):
            continue
        clean = "".join(ch for ch in w["word"].lower() if ch.isalpha())
        word = w["word"].strip()
        if clean and safety.SWEAR.match(clean):
            word = word[0] + "*" * max(2, len(word) - 1)
        out.append({"word": word, "start": w["start"], "end": w["end"]})
    return out


def trim(wav):
    """Cut silence at both ends so a line never runs longer than its words."""
    tmp = wav.with_suffix(".t.wav")
    ms.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af",
            "silenceremove=start_periods=1:start_threshold=-42dB,areverse,"
            "silenceremove=start_periods=1:start_threshold=-42dB,areverse", str(tmp)])
    tmp.replace(wav)
    return duration(wav)


def run(plan, out):
    report = {"name": plan["name"], "clips": []}
    engine = plan.get("voice_engine", "qwen")
    instruct = plan.get("instruct", "Excited, energetic young gamer reacting to the action")
    for clip in plan["clips"]:
        cid = clip["id"]
        work = Path("work") / f"c{cid:02d}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            if clip.get("urls"):            # listicle: join the parts first
                parts = [fetch(u, work / f"p{k}.mp4") for k, u in enumerate(clip["urls"])]
                lst = work / "parts.txt"
                lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
                src = work / "in.mp4"
                ms.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", str(src)])
            else:
                src = fetch(clip["url"], work / "in.mp4")
            if clip.get("cut"):             # use only [a, b] seconds of a longer downloaded window
                a, b = clip["cut"]
                cutf = work / "cut.mp4"
                ms.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a), "-i", str(src), "-t", str(b - a),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(cutf)])
                src = cutf
            dur = duration(src)
            if plan["mode"] != "grab":       # safety: no nudity, no swearing in anything we publish
                import safety
                ok, bad = safety.is_clean(src)
                if not ok:
                    raise RuntimeError("NSFW: nudity at " + ", ".join(f"{a:.0f}-{b:.0f}s {l}" for a, b, l, _ in bad))
                clean = work / "clean.mp4"
                n_bad, words = safety.mute_profanity(src, clean)
                if n_bad:
                    print(f"Clip {cid}: muted {n_bad} swear word(s)", flush=True)
                src = clean
                vw, vh, fps = ms.probe(src)
                if vw > vh:                  # landscape gameplay: hide game subtitles, then make it vertical
                    bl = work / "nosubs.mp4"
                    ms.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex",
                            "[0:v]split[a][b];[b]crop=iw:ih*0.17:0:ih*0.80,boxblur=22:2[s];"
                            "[a][s]overlay=0:main_h*0.80[v]", "-map", "[v]", "-map", "0:a:0?",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-c:a", "copy", str(bl)])
                    layout = clip.get("layout") or plan.get("default_layout", "square")
                    aspect = ms.LAYOUTS.get(layout, 1.0)
                    fg_h = min(ms.H, int(round(ms.W / aspect / 2) * 2))
                    fg_bottom = max(0, (ms.H - fg_h) // 2 - (80 if layout != "full" else 0)) + fg_h
                    hass = work / "hook.ass"
                    hass.write_text(ms.build_ass([], clip.get("hook"), fg_bottom, dur), encoding="utf-8")
                    vert = work / "vertical.mp4"
                    ms.render(bl, hass, layout, vert, fps)
                    src = vert
            if plan["mode"] == "grab":
                sheet = out / f"strip_{cid:02d}.jpg"
                ms.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
                        "fps=1,scale=240:-2,tile=8x5", "-frames:v", "1", "-q:v", "4", str(sheet)])
                report["clips"].append({"id": cid, "seconds": dur, "file": sheet.name})
                print(f"Strip {cid} done", flush=True)
                continue
            shift = 0.0
            if clip.get("teaser"):          # cold open: flash the peak moment first, then play from the start
                ts, tl = clip["teaser"]
                tz = work / "teaser.mp4"
                ms.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex",
                        f"[0:v]trim={ts}:{ts + tl},setpts=PTS-STARTPTS[tv];[0:a]atrim={ts}:{ts + tl},asetpts=PTS-STARTPTS[ta];"
                        "[tv][ta][0:v][0:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(tz)])
                src, shift = tz, float(tl)
                dur = duration(src)
            cur, used = src, []
            for n, line in enumerate(clip.get("lines", [])):
                wav = work / f"l{n}.wav"
                length, how = ms.make_voice(line["text"], wav, engine=engine, instruct=clip.get("instruct", instruct))
                length = trim(wav)
                t = min(float(line["t"]) + shift, max(0.3, dur - 4.5 - length))
                nxt = work / f"v{n}.mp4"
                ms.add_voice(cur, wav, t, nxt)
                cur = nxt
                used.append({"t": round(t, 1), "text": line["text"], "voice": how, "len": round(length, 1)})
            q = clip["question"]
            if q.get("say"):
                wav = work / "q.wav"
                length, how = ms.make_voice(q["say"], wav, engine=engine, instruct=clip.get("instruct", instruct))
                length = trim(wav)
                qt = max(0.5, dur - length - 0.4)
                if used and used[-1]["t"] + used[-1]["len"] + 0.3 > qt:
                    print(f"Clip {cid}: last line would overlap the question; moved earlier", flush=True)
                nxt = work / "vq.mp4"
                ms.add_voice(cur, wav, qt, nxt)
            else:                           # question on screen only, no voice
                length, how, qt, nxt = 3.5, "none", max(0.5, dur - 3.8), cur
            ass = work / "q.ass"
            question_ass(q["text"], max(0.0, qt - 0.3), dur, ass)
            # word-by-word captions: our narration, plus (optionally) what the characters say
            busy = [(u["t"], u["t"] + u["len"]) for u in used] + [(qt - 0.3, dur)]
            cap_words = narration_words(used)
            if clip.get("game_captions"):
                cap_words = sorted(cap_words + [w for w in game_words(nxt, busy) if w["start"] < qt - 0.4],
                                   key=lambda w: w["start"])
            layout = clip.get("layout") or plan.get("default_layout", "square")
            aspect = ms.LAYOUTS.get(layout, 1.0)
            fg_h = min(ms.H, int(round(ms.W / aspect / 2) * 2))
            fg_bottom = max(0, (ms.H - fg_h) // 2 - (80 if layout != "full" else 0)) + fg_h
            caps = work / "caps.ass"
            caps.write_text(ms.build_ass([w for w in cap_words if w["end"] <= qt - 0.3], None, fg_bottom, dur),
                            encoding="utf-8")
            final = out / f"short_{cid:02d}.mp4"
            ms.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(nxt), "-vf",
                    f"subtitles='{caps}':fontsdir='{ms.FONTS_DIR}',subtitles='{ass}':fontsdir='{ms.FONTS_DIR}',"
                    f"scale={ms.OUT_W}:{ms.OUT_H}:flags=lanczos,format=yuv420p", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "16", "-maxrate", "35M", "-bufsize", "70M", "-c:a", "copy", "-movflags", "+faststart", str(final)])
            ms.frames(final, out, f"short_{cid:02d}", dur, n=3)
            used.append({"t": round(qt, 1), "text": q.get("say", ""), "voice": how, "len": round(length, 1)})
            report["clips"].append({"id": cid, "file": final.name, "seconds": dur, "voice": used,
                                    "size_mb": round(final.stat().st_size / 1e6, 1)})
            print(f"Short {cid} done: {len(used)} voice lines", flush=True)
        except Exception as e:
            print(f"Clip {cid} FAILED: {e}", flush=True)
            report["clips"].append({"id": cid, "error": str(e)})
        finally:                            # 4K intermediates are big: free the disk after every clip
            shutil.rmtree(work, ignore_errors=True)
            (out / "report.json").write_text(json.dumps(report, indent=1))
    (out / "report.json").write_text(json.dumps(report, indent=1))
