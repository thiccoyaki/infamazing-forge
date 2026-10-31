"""Formats (prompt files), research + script writing, length fit, revise. All through the claude CLI."""
import re

from . import config as C, db, llm, yt

RESEARCH_TOOLS = ["WebSearch", "WebFetch"]


# ---------- formats: formats/<id>.md  (line 1 "# Name", line 2 description, a "chars: MIN-MAX" line)

def list_formats() -> list[dict]:
    out = []
    for p in sorted(C.FORMATS_DIR.glob("*.md")):
        if p.name.startswith("_"):
            continue
        out.append({"id": p.stem, **parse_format(p.read_text(encoding="utf-8"), p.stem)})
    return out


def parse_format(body: str, fid: str = "") -> dict:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    name = lines[0].lstrip("# ").strip() if lines else fid
    desc = lines[1] if len(lines) > 1 and not lines[1].lower().startswith("chars:") else ""
    m = re.search(r"^chars:\s*(\d+)\s*-\s*(\d+)", body, re.M | re.I)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (750, 825)
    return {"name": name, "description": desc[:200], "min_chars": lo, "max_chars": hi}


def format_path(fid: str):
    if not re.fullmatch(r"[a-z0-9\-]+", fid or ""):
        raise ValueError("format id: lowercase letters, numbers and dashes only")
    return C.FORMATS_DIR / f"{fid}.md"


def read_format(fid: str) -> str:
    p = format_path(fid)
    if not p.exists():
        raise FileNotFoundError(f"No format '{fid}'")
    return p.read_text(encoding="utf-8")


def write_format(fid: str, body: str) -> None:
    format_path(fid).write_text(body, encoding="utf-8")


def global_rules() -> str:
    p = C.FORMATS_DIR / "_global.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def default_format() -> str:
    fs = list_formats()
    want = db.get_setting("default_format", "")
    return want if any(f["id"] == want for f in fs) else (fs[0]["id"] if fs else "")


# ---------- helpers

def clean_script(text: str) -> str:
    t = re.sub(r"```.*?```", "", text.strip(), flags=re.S).strip()
    t = " ".join(ln.strip() for ln in t.splitlines() if ln.strip())
    t = t.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ")
    return re.sub(r"\s{2,}", " ", t).strip().strip('"').strip()


def clean_title(t: str) -> str:
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (t or "").strip()).strip(". ")
    t = re.sub(r"\s+", " ", t)
    return (t[:70].rsplit(" ", 1)[0] if len(t) > 70 else t).strip(". ") or "Untitled"


def style_block(ref_ids: list[int]) -> str:
    if ref_ids:
        db.touch_refs(ref_ids)
    parts = []
    for rid in ref_ids:
        r = db.get_ref(rid)
        if r:
            parts.append(f"--- {r['title']} ({len(r['body'])} chars) ---\n{r['body']}")
    return "\n\n".join(parts)


def auto_refs(fmt: str, n: int = 3) -> list[int]:
    """No refs picked: use the most recent past scripts of the same format (falls back to any)."""
    refs = db.list_refs()
    same = [r["id"] for r in refs if r["format"] == fmt]
    return (same or [r["id"] for r in refs])[:n]


# ---------- generate

def generate(topic: str, angle: str, fmt: str, ref_ids: list[int], notes: str, llm_model: str, log: list) -> dict:
    f = parse_format(read_format(fmt), fmt)
    ref_ids = ref_ids or auto_refs(fmt)
    refs = style_block(ref_ids)
    taken = db.all_titles()[:300]
    prompt = "\n\n".join(x for x in [
        "You write YouTube Shorts scripts for the trivia channel Infamazing.",
        "## GLOBAL RULES\n" + global_rules(),
        "## FORMAT\n" + read_format(fmt),
        "## PAST SCRIPTS / REFERENCES (match rhythm, tone and level of detail; never copy facts or sentences)\n" + refs if refs else "",
        f"## TOPIC\n{topic.strip()}" + (f"\nAngle: {angle.strip()}" if angle.strip() else ""),
        f"## EXTRA NOTES (must be honored)\n{notes.strip()}" if notes.strip() else "",
        f"""## STEPS
1. Research the topic with WebSearch / WebFetch. Verify every fact you will use against at least one reliable
   source. If the premise is wrong, keep the topic but switch to the true, still-surprising angle.
2. Find reference links for the editor: first look for 1-3 YouTube videos covering this exact story
   (search "<topic> youtube"). If there is no relevant YouTube video, give 2-3 non-YouTube sources instead
   (news, Wikipedia, official pages). Only real URLs you actually saw in search results.
3. Write the script following the FORMAT exactly. Length: {f['min_chars']}-{f['max_chars']} characters, count carefully.
4. Write the best SEO title for the Short (under 70 characters, curiosity + main keyword, no emojis, no hashtags).
   It is also the folder and file name, so no characters like / \\ : * ? " < > |.
   Don't reuse any of these existing titles: {"; ".join(taken[:120]) or "none"}

Reply with JSON only:
{{"title": "...", "script": "...", "facts": ["each verified fact used, with its source domain"],
  "references": [{{"title": "...", "url": "https://...", "kind": "youtube|web"}}],
  "premise_note": "only if the original idea was wrong: what was wrong and what you used instead, else empty"}}""",
    ] if x)
    log.append(f"researching + writing ({fmt}, {len(ref_ids)} style refs)…")
    data = llm.ask_json(prompt, model=llm_model, tools=RESEARCH_TOOLS, timeout=1200)
    if not isinstance(data, dict) or not data.get("script"):
        raise llm.ClaudeError("Claude returned no script")
    script = clean_script(data["script"])
    script = fit_length(script, f["min_chars"], f["max_chars"], fmt, llm_model, log)
    refs_out = [r for r in data.get("references") or [] if isinstance(r, dict) and str(r.get("url", "")).startswith("http")]
    if not any(r.get("kind") == "youtube" or "youtu" in r["url"] for r in refs_out):
        try:  # Claude found none: one more look with the YouTube API, if a key is set
            hits = yt.search(data.get("title") or topic, 3)
            if hits:
                log.append(f"added {len(hits)} YouTube result(s) from the YouTube API")
                refs_out = [{"title": h["title"], "url": h["url"], "kind": "youtube"} for h in hits] + refs_out
        except Exception as e:  # noqa: BLE001
            log.append(f"YouTube API search skipped: {e}")
    if data.get("premise_note"):
        log.append("premise note: " + data["premise_note"])
    return {"title": " ".join(str(data.get("title") or topic).split())[:100], "script": script,
            "facts": [str(x) for x in data.get("facts") or []], "refs": refs_out, "style_refs": ref_ids,
            "notes": data.get("premise_note") or ""}


def fit_length(script: str, lo: int, hi: int, fmt: str, llm_model: str, log: list) -> str:
    best = script
    target = (lo + hi) // 2
    for i in range(C.LENGTH_TOLERANCE_PASSES):
        n = len(script)
        if abs(n - target) < abs(len(best) - target):
            best = script
        if lo <= n <= hi:
            log.append(f"length ok: {n} chars ({lo}-{hi})")
            return script
        verb = "SHORTEN" if n > hi else "LENGTHEN"
        log.append(f"pass {i + 1}: {n} chars, {verb.lower()} to {lo}-{hi}")
        prompt = ("## GLOBAL RULES\n" + global_rules() + "\n\n## FORMAT\n" + read_format(fmt) +
                  f"\n\n## TASK\nThe script below is {n} characters. {verb} it to between {lo} and {hi} characters "
                  f"(about {abs(n - target)} characters). Keep every fact, the format structure, the CTA and the ending. "
                  + ("Cut filler and merge sentences." if n > hi else "Add concrete verified detail already implied by the facts, no new claims.")
                  + "\nReply with ONLY the revised script as one paragraph.\n\n## SCRIPT\n" + script)
        script = clean_script(llm.ask(prompt, model=llm_model))
    if lo <= len(script) <= hi:
        return script
    log.append(f"closest after {C.LENGTH_TOLERANCE_PASSES} passes: {len(best)} chars")
    return best


def revise(video: dict, instruction: str, llm_model: str, log: list, research: bool = False) -> str:
    f = parse_format(read_format(video["format"]), video["format"]) if video["format"] else {"min_chars": 0, "max_chars": 99999}
    prompt = ("## GLOBAL RULES\n" + global_rules() +
              (f"\n\n## FORMAT\n{read_format(video['format'])}" if video["format"] else "") +
              f"\n\n## VERIFIED FACTS\n" + "\n".join("- " + x for x in video.get("facts") or []) +
              f"\n\n## TASK\n{instruction.strip()}\nStay within {f['min_chars']}-{f['max_chars']} characters."
              + (" Verify any new fact with web search first." if research else " Don't add facts that aren't verified above.")
              + "\nReply with ONLY the revised script as one paragraph.\n\n## SCRIPT\n" + video["script"])
    log.append("revising…")
    out = clean_script(llm.ask(prompt, model=llm_model, tools=RESEARCH_TOOLS if research else None, timeout=900))
    if video["format"]:
        out = fit_length(out, f["min_chars"], f["max_chars"], video["format"], llm_model, log)
    return out


def est_seconds(text: str, voice_id: str = "") -> float:
    return round(len(text) / db.get_pace(voice_id), 1)
