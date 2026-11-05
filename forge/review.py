"""Script review, after Legion's editor (HANDOFF-infamazing-forge.md §2), in its port priority:
blind line-up editor (rubric when there are no references) -> keep-only-if-better rewrite loop -> AI-tell regex
-> repeat guard against the channel's past scripts."""
import random
import re

from . import db, llm, scripts

ROUNDS = 2

# ---------- AI tells (regex, no model call)

TELLS = [
    (r"\bnot (?:just |only |merely )?[^.,;!?]{1,40},\s*(?:but|it's|it was)\b", "not X, but Y"),
    (r"\b(?:isn't|wasn't|aren't) (?:just )?[^.,;!?]{1,40}[.,]\s*(?:it's|it was|they're)\b", "not X, but Y"),
    (r"(?:^|[.!?]\s+)(?:The (?:result|answer|catch|twist|kicker|truth|reason)|Why|How)\?\s+[A-Z][^.!?]{0,25}[.!]", "rhetorical Q + punch answer"),
    (r"\b(\w+), (\w+), and (\w+)\.", "list of three single words"),
    (r"\b(?:here's the (?:thing|kicker|twist)|let that sink in|buckle up|little did (?:they|he|she) know|"
     r"in a world where|it's worth noting|game[- ]changer|the rest is history|and that's not all|"
     r"you won't believe|plot twist)\b", "stock phrase"),
    (r"[—–]", "em dash"),
]


def ai_tells(text: str) -> list[dict]:
    out = []
    for pat, name in TELLS:
        for m in re.finditer(pat, text, re.I):
            out.append({"tell": name, "text": m.group(0).strip()[:120]})
    # stacked fragments: 3+ sentences in a row under 4 words
    sents = re.split(r"(?<=[.!?])\s+", text)
    run = []
    for s in sents + [""]:
        if s and len(s.split()) < 4:
            run.append(s)
            continue
        if len(run) >= 3:
            out.append({"tell": "stacked fragments", "text": " ".join(run)[:120]})
        run = []
    return out


def fix_tells(script: str, tells: list[dict], llm_model: str, log: list) -> str:
    if len(tells) < 2:
        return script
    log.append(f"AI tells: {len(tells)} ({', '.join(sorted({t['tell'] for t in tells}))}), rewriting those sentences…")
    prompt = ("Rewrite ONLY the flagged parts of this YouTube Shorts narration so they sound like a person talking, "
              "not an AI. Keep every number, name and fact, and every other sentence exactly as it is. "
              "No em dashes. Reply with ONLY the full script as one paragraph.\n\nFLAGGED:\n"
              + "\n".join(f"- [{t['tell']}] {t['text']}" for t in tells) + "\n\nSCRIPT:\n" + script)
    return scripts.clean_script(llm.ask(prompt, model=llm_model))


# ---------- repeat guard (vs the channel's past scripts)

def _ngrams(text: str, n: int = 6) -> set:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def repeat_guard(script: str, fmt: str, exclude_title: str = "") -> list[str]:
    """Phrases this script shares with past scripts. Phrases the format itself prescribes (its CTA, its closing
    line) are expected and don't count."""
    allowed = _ngrams(scripts.read_format(fmt)) if fmt else set()
    past = [v["script"] for v in db.list_videos() if v["title"] != exclude_title and v["script"]]
    past += [r["body"] for r in db.list_refs() if r["source"] in ("past", "csv")]
    mine = _ngrams(script) - allowed
    notes = []
    first = " ".join(script.split()[:8]).lower()
    for p in past:
        if " ".join(p.split()[:8]).lower() == first:
            notes.append(f"same opening as a past script: \"{' '.join(script.split()[:8])}…\"")
            break
    shared = {}
    for p in past:
        for g in mine & _ngrams(p):
            shared[g] = shared.get(g, 0) + 1
    if shared:
        top = sorted(shared, key=lambda g: -shared[g])[:6]
        notes.append("phrases reused from past scripts: " + "; ".join(f"\"{g[:60]}\"" for g in top))
    return notes


# ---------- editor

EDITOR = ("You are a harsh YouTube Shorts editor. You did NOT write any of these and have no reason to like them. ")


def blind_test(script: str, refs: list[str], llm_model: str) -> dict:
    """Hide the script among up to 4 reference scripts; the editor must spot the imitator."""
    lineup = refs[:4] + [script]
    random.shuffle(lineup)
    mine = lineup.index(script)
    prompt = (EDITOR + "These Shorts scripts are by one channel, except ONE written by an imitator trying to copy it. "
              "Find the imitator.\n\n" + "\n\n".join(f"[{i}] {s}" for i, s in enumerate(lineup)) +
              '\n\nReply with JSON only: {"imitator": <index>, "confidence": <0-100>, '
              '"tells": ["what gave it away, each a concrete fix note"]}')
    d = llm.ask_json(prompt, model=llm_model)
    picked = int(d.get("imitator", -1)) if str(d.get("imitator", "")).lstrip("-").isdigit() else -1
    conf = int(d.get("confidence", 0) or 0)
    caught = picked == mine and conf >= 50
    return {"kind": "blind", "caught": caught, "confidence": conf if picked == mine else 0,
            "notes": [str(t) for t in d.get("tells") or []] if picked == mine else [],
            "passed": not caught, "score": 100 - (conf if picked == mine else 0)}


RUBRIC = ["hook", "setup", "chain", "payoff", "voice", "spoken"]


def rubric(script: str, fmt: str, llm_model: str) -> dict:
    prompt = (EDITOR + "Grade this Shorts script 0-10 on: hook (stops the scroll in 1 second), setup (clear stakes), "
              "chain (each beat bigger than the last), payoff (the reveal lands), voice (sounds like a person, not AI), "
              "spoken (easy to read aloud). Calibrate: a top-performing Short in this format is a 9. Don't flag what the "
              "format itself requires.\n\nFORMAT:\n" + (scripts.read_format(fmt) if fmt else "-") + "\n\nSCRIPT:\n" + script +
              '\n\nReply with JSON only: {"scores": {"hook": 0, "setup": 0, "chain": 0, "payoff": 0, "voice": 0, "spoken": 0}, '
              '"notes": ["concrete fix notes for the weakest parts"]}')
    d = llm.ask_json(prompt, model=llm_model)
    sc = {k: int((d.get("scores") or {}).get(k, 0) or 0) for k in RUBRIC}
    low = min(sc.values())
    return {"kind": "rubric", "scores": sc, "score": low * 10, "passed": low >= 8,
            "notes": [str(n) for n in d.get("notes") or []]}


def judge(script: str, fmt: str, refs: list[str], llm_model: str) -> dict:
    return blind_test(script, refs, llm_model) if refs else rubric(script, fmt, llm_model)


def review(script: str, fmt: str, ref_ids: list[int], facts: list[str], llm_model: str, log: list,
           title: str = "") -> tuple[str, dict]:
    """Tells fix -> judge -> up to ROUNDS rewrites, each kept only if it judges better and stays in length.
    Returns (best script, review report)."""
    f = scripts.parse_format(scripts.read_format(fmt), fmt) if fmt else {"min_chars": 0, "max_chars": 10 ** 6}
    in_range = lambda s: f["min_chars"] <= len(s) <= f["max_chars"]  # noqa: E731
    refs = [r["body"] for r in (db.get_ref(i) for i in ref_ids) if r][:4]
    tells = ai_tells(script)
    fixed = fix_tells(script, tells, llm_model, log)
    if fixed != script and in_range(fixed):
        script = fixed
    best, verdict = script, judge(script, fmt, refs, llm_model)
    log.append(_say(verdict))
    rounds = 0
    while not verdict["passed"] and rounds < ROUNDS:
        rounds += 1
        notes = verdict["notes"] + repeat_guard(best, fmt, title)
        prompt = ("## GLOBAL RULES\n" + scripts.global_rules() + (f"\n\n## FORMAT\n{scripts.read_format(fmt)}" if fmt else "")
                  + "\n\n## VERIFIED FACTS (use only these)\n" + "\n".join("- " + x for x in facts)
                  + "\n\n## EDITOR NOTES (fix all; structure may change)\n" + "\n".join("- " + n for n in notes)
                  + f"\n\n## TASK\nRewrite the script. Stay within {f['min_chars']}-{f['max_chars']} characters. "
                  "Reply with ONLY the script as one paragraph.\n\n## SCRIPT\n" + best)
        cand = scripts.clean_script(llm.ask(prompt, model=llm_model))
        if fmt and not in_range(cand):
            cand = scripts.fit_length(cand, f["min_chars"], f["max_chars"], fmt, llm_model, log)
        v2 = judge(cand, fmt, refs, llm_model)
        if v2["score"] > verdict["score"] and in_range(cand):
            best, verdict = cand, v2
            log.append(f"round {rounds}: better, kept. " + _say(v2))
        else:
            log.append(f"round {rounds}: not better ({v2['score']} vs {verdict['score']}), kept the previous one")
    if not verdict["passed"]:
        log.append("editor still not satisfied: check the notes before VO")
    report = {**verdict, "rounds": rounds, "tells_left": ai_tells(best), "repeats": repeat_guard(best, fmt, title)}
    return best, report


def _say(v: dict) -> str:
    if v["kind"] == "blind":
        return f"blind test: {'CAUGHT' if v['caught'] else 'passed'} (editor confidence {v['confidence']}%)"
    return f"rubric: weakest {min(v['scores'].values())}/10 {'pass' if v['passed'] else 'fail'} {v['scores']}"


if __name__ == "__main__":  # self-check for the regex parts
    t = ai_tells("It's not a snack, but a weapon. The result? Chaos. Big. Loud. Wild. Fast, cheap, and loud. Buckle up.")
    names = {x["tell"] for x in t}
    assert {"not X, but Y", "rhetorical Q + punch answer", "stacked fragments", "list of three single words", "stock phrase"} <= names, names
    assert not ai_tells("In 1962, Sam Panopoulos ran a small diner in Chatham, Ontario, serving burgers and fries.")
    print("ok", sorted(names))
