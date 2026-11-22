"""Self-learning pronunciation layer for the AI voice.

lexicon.json : word -> {"say": respelling, "ok": successes, "fail": failures}
errors.jsonl : one line per check (engine, script, what Whisper heard, missed/extra words, fix tried, result)

Flow per voice line:
  1. apply known respellings from the lexicon
  2. generate (Qwen), Whisper listens back, compare word by word with the script
  3. wrong/missing words -> try the next respelling variant for just those words
  4. success after a fix -> the respelling is saved to the lexicon for next time
  5. after MAX_TRIES failures -> Kokoro backup, and the failure is logged
"""
import difflib, json, os, re, time

LEX = "voice/lexicon.json"
LOG = "voice/errors.jsonl"
MAX_TRIES = 4


def norm_words(t):
    t = t.lower().replace("gta 6", "gta six").replace("gta vi", "gta six").replace("gta6", "gta six")
    t = t.replace("-", " ").replace("'", "")
    return re.sub(r"[^a-z0-9 ]", " ", t).split()


def diff(script, heard):
    a, b = norm_words(script), norm_words(heard)
    missed, extra = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op in ("replace", "delete"):
            missed += a[i1:i2]
        if op in ("replace", "insert"):
            extra += b[j1:j2]
    return missed, extra


def variants(word):
    """Respelling ideas, tried in order, for a word the model got wrong."""
    w = word.lower()
    out = []
    m = re.match(r"^(.*?[aeiouy][^aeiouy]*)(ed|ing|er|s)$", w)
    if m:
        out.append(f"{m.group(1)}-{m.group(2)}")          # wreck-ed
    sounds = [("wr", "r"), ("ph", "f"), ("ck", "k"), ("cked", "ckt"), ("ed$", "t"), ("tion", "shun"), ("gh", "")]
    s = w
    for a, b in sounds:
        s = re.sub(a, b, s)
    if s != w:
        out.append(s)                                       # rekt-style phonetic spelling
    out.append(w.upper())                                   # stress the word
    out.append(f", {w},")                                   # slow down around it
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


class Learner:
    def __init__(self):
        self.lex = json.load(open(LEX)) if os.path.exists(LEX) else {}

    def best(self, word):
        """Best known spelling for a word: the variant with the most wins over losses."""
        e = self.lex.get(word.lower())
        if not e:
            return None
        score = {v: st["ok"] - st["fail"] for v, st in e.get("variants", {}).items()}
        v = max(score, key=score.get) if score else None
        return v if v and score[v] > 0 else None

    def apply(self, text):
        return re.sub(r"[A-Za-z']+", lambda m: self.best(m.group(0)) or m.group(0), text)

    def respell(self, text, word, variant):
        return re.sub(rf"\b{re.escape(word)}\b", variant, text, flags=re.I)

    def log(self, **kw):
        kw["t"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    def record(self, word, say, ok):
        e = self.lex.setdefault(word.lower(), {"variants": {}})
        st = e.setdefault("variants", {}).setdefault(say, {"ok": 0, "fail": 0})
        st["ok" if ok else "fail"] += 1
        e["best"] = self.best(word)

    def save(self):
        json.dump(self.lex, open(LEX, "w"), indent=1, sort_keys=True)

    def speak(self, script, generate, listen, fallback, instruct_steps=None):
        """generate(text)->wav, listen(wav)->heard text, fallback(text)->wav. Returns (engine, tries)."""
        fixes, used = {}, {}
        for attempt in range(1, MAX_TRIES + 1):
            text = self.apply(script)
            for w, v in fixes.items():
                text = self.respell(text, self.best(w) or w, v)
            heard = listen(generate(text))
            missed, extra = diff(script, heard)
            ok = not missed and not extra
            self.log(engine="qwen", attempt=attempt, script=script, said=text, heard=heard,
                     missed=missed, extra=extra, ok=ok)
            for w, v in fixes.items():
                self.record(w, v, w not in missed)
            if ok:
                self.save()
                return "qwen", attempt
            for w in set(missed):
                used.setdefault(w, []).append(fixes.get(w, self.best(w) or w))
                nxt = next((v for v in variants(w) if v not in used[w]), None)
                if nxt:
                    fixes[w] = nxt
                else:
                    fixes.pop(w, None)
        fallback(script)
        self.log(engine="kokoro", script=script, note=f"fallback after {MAX_TRIES} tries")
        self.save()
        return "kokoro", MAX_TRIES
