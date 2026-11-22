import glob, re, numpy as np, soundfile as sf, librosa
from faster_whisper import WhisperModel
lines = [l.strip() for l in open("scripts/tts/lines.txt") if l.strip()]
m = WhisperModel("small", device="cpu", compute_type="int8")
norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower().replace("gta 6", "gta six").replace("gta6","gta six")).split()
def wer(ref, hyp):
    r, h = norm(ref), norm(hyp); d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j-1] + 1, prev + (r[i-1] != h[j-1]))
    return d[-1] / max(1, len(r))
for e in ["kokoro", "chatterbox", "turbo", "qwen"]:
    ws, pv = [], []
    for i, ref in enumerate(lines):
        f = f"aud/{e}_{i}.wav"; y, sr = librosa.load(f, sr=16000)
        txt = " ".join(s.text for s in m.transcribe(y, language="en")[0])
        ws.append(wer(ref, txt))
        f0, vf, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr); f0 = f0[~np.isnan(f0)]
        pv.append(float(np.std(12*np.log2(f0/np.median(f0)))) if len(f0) > 10 else 0)
        print(f"{e} #{i} WER={ws[-1]:.2f} pitchvar={pv[-1]:.2f}st | {txt.strip()}")
    print(f"SUMMARY {e}: avg WER {np.mean(ws):.2f}, worst {max(ws):.2f}, pitch variation {np.mean(pv):.2f} semitones")
