"""Video ideas: brainstorm from a direction + past titles, or niche-bend competitor outliers into Infamazing topics."""
from . import db, llm, scripts


def _common(count: int, direction: str, check: bool) -> str:
    fmts = "\n".join(f"- {f['id']}: {f['name']}. {f['description']}" for f in scripts.list_formats())
    taken = db.all_titles()
    return f"""Infamazing is a faceless YouTube Shorts trivia channel (food, candy, toys, fast food brands, theme parks,
pop culture): superlatives (most expensive / most dangerous / most hated), "which came first", "why X is called Y",
brand blunders, dark food lore, origin stories. Ideas must be TRUE and have a surprising, emotional "no way" core.
{"Check each premise with a quick web search before you pitch it, and drop any that turn out false." if check else ""}
{f"Direction from the creator: {direction.strip()}" if direction.strip() else ""}

Script formats:
{fmts}

Already covered or pitched (never repeat or near-repeat these):
{chr(10).join('- ' + t for t in taken[:400]) or '- none'}

Each idea: "title" = a working SEO-style Short title, "angle" = the surprising fact / reveal in one line,
"format" = best format id from the list. Write exactly {count} ideas, strongest first."""


def brainstorm(count: int, direction: str, check: bool, llm_model: str, log: list) -> list[dict]:
    log.append(f"brainstorming {count} ideas…")
    prompt = _common(count, direction, check) + """
Reply with JSON only: {"ideas": [{"title": "...", "angle": "...", "format": "..."}]}"""
    data = llm.ask_json(prompt, model=llm_model, tools=["WebSearch"] if check else None, timeout=900)
    return _save(data, "brainstorm", log)


MODES = {
    "adapt": """ADAPT these proven outlier Shorts from other channels: keep each one's TRUE story / topic, research
what actually happened, and re-pitch it as an Infamazing Short in OUR structure (pick the best of our formats).
Find our own angle: the most surprising verified fact, a superlative, a "which came first" or a reveal. The title
must be ours, never theirs. Skip any whose story we've already covered.""",
    "winners": """These are OUR OWN latest Shorts that beat our channel median. For each, work out WHY it won (the
topic vein, the brand or item, the emotion, the hook, the format). Then pitch follow-ups that ride the same winning
vein: the same angle on a different brand / item / country, the rival's version ("and Pepsi did it too"), a deeper
or darker part 2 of the same story, the flip side or opposite superlative, or a "which came first / which is bigger"
pairing. Every idea must be a NEW true story we haven't covered, and lean hardest on the biggest winners.""",
    "bend": """NICHE-BEND these proven outlier Shorts from other channels: name why each one worked (hook mechanism,
curiosity gap, emotional payoff), keep that DNA, and swap the subject to a DIFFERENT true story that fits Infamazing.
Never copy their topic.""",
}


def from_outliers(videos: list[dict], count: int, direction: str, check: bool, llm_model: str, log: list,
                  mode: str = "adapt") -> list[dict]:
    src = "\n".join(f"[{i}] \"{v['title']}\" ({v['channel']}, {v['views']:,} views, {v['multiple']}x channel median)"
                    + (f"\n    our script: {_our_script(v['title'])[:900]}" if mode == "winners" and _our_script(v["title"]) else "")
                    for i, v in enumerate(videos))
    log.append(f"{ {'adapt': 'adapting', 'winners': 'following up'}.get(mode, 'bending') } {len(videos)} video(s) into {count} ideas…")
    prompt = _common(count, direction, check) + f"""

{MODES.get(mode, MODES['adapt'])}

Outliers:
{src}

Reply with JSON only: {{"ideas": [{{"title": "...", "angle": "...", "format": "...", "source": <outlier index>}}]}}"""
    data = llm.ask_json(prompt, model=llm_model, tools=["WebSearch"] if check else None, timeout=900)
    for it in (data.get("ideas", []) if isinstance(data, dict) else []):
        try:
            v = videos[int(it.get("source"))]
            it["src"] = f"{mode}: {v['title']} ({v['channel']}, {v['multiple']}x) {v['url']}"
        except (TypeError, ValueError, IndexError):
            pass
    return _save(data, "my winners" if mode == "winners" else "outliers", log)


def _norm(t: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _our_script(title: str) -> str:
    """The script behind one of our published Shorts, if it's in the app (matched by title)."""
    n = _norm(title)
    for v in db.list_videos():
        if _norm(v["title"]) == n:
            return v["script"]
    for r in db.list_refs():
        if _norm(r["title"]) == n:
            return r["body"]
    return ""


def _save(data, kind: str, log: list) -> list[dict]:
    ids = {f["id"] for f in scripts.list_formats()}
    out = []
    for it in (data.get("ideas", []) if isinstance(data, dict) else []):
        if not isinstance(it, dict) or not it.get("title"):
            continue
        fmt = it.get("format") if it.get("format") in ids else scripts.default_format()
        iid = db.add_idea(str(it["title"]).strip(), str(it.get("angle", "")).strip(), fmt, it.get("src") or kind)
        out.append(db.get_idea(iid))
    log.append(f"{len(out)} idea(s) added")
    return out
