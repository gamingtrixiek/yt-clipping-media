import sys, time, os, numpy as np, soundfile as sf
engine = sys.argv[1]; lines = [l.strip() for l in open("scripts/tts/lines.txt") if l.strip()]
os.makedirs("out", exist_ok=True); t0 = time.time()
def save(i, wav, sr): sf.write(f"out/{engine}_{i}.wav", np.asarray(wav, dtype=np.float32).squeeze(), sr)
if engine == "kokoro":
    from kokoro import KPipeline
    p = KPipeline(lang_code="a"); load = time.time() - t0
    for i, l in enumerate(lines):
        save(i, np.concatenate([a for _, _, a in p(l, voice="am_michael", speed=1.08)]), 24000)
elif engine == "chatterbox":
    from chatterbox.tts import ChatterboxTTS
    m = ChatterboxTTS.from_pretrained(device="cpu"); load = time.time() - t0
    for i, l in enumerate(lines):
        w = m.generate(l, exaggeration=0.75, cfg_weight=0.35); save(i, w.cpu().numpy(), m.sr)
elif engine == "turbo":
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    m = ChatterboxTurboTTS.from_pretrained(device="cpu"); load = time.time() - t0
    tagged = [lines[0] + " [chuckle]", lines[1], lines[2], lines[3] + " [laugh]"]
    for i, l in enumerate(tagged):
        try: w = m.generate(l)
        except TypeError: w = m.generate(l, audio_prompt_path="ref.wav")
        save(i, w.cpu().numpy(), m.sr)
elif engine == "qwen":
    import torch
    from qwen_tts import Qwen3TTSModel
    name = os.environ.get("QWEN", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    m = Qwen3TTSModel.from_pretrained(name, device_map="cpu", dtype=torch.float32); load = time.time() - t0
    for i, l in enumerate(lines):
        wavs, sr = m.generate_custom_voice(text=l, language="English", speaker="Ryan",
                                           instruct="Excited, energetic young gamer reacting to an awesome moment")
        save(i, wavs[0], sr)
gen = time.time() - t0 - load
print(f"RESULT {engine}: model load {load:.0f}s, 4 lines {gen:.0f}s ({gen/4:.1f}s per line)")
