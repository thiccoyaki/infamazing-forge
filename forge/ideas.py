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


def from_outliers(videos: list[dict], count: int, direction: str, check: bool, llm_model: str, log: list) -> list[dict]:
    src = "\n".join(f"[{i}] \"{v['title']}\" ({v['channel']}, {v['views']:,} views, {v['multiple']}x channel median)"
                    for i, v in enumerate(videos))
    log.append(f"bending {len(videos)} outlier(s) into {count} ideas…")
    prompt = _common(count, direction, check) + f"""

Use NICHE BENDING on these proven outlier Shorts from other channels: name why each one worked (hook mechanism,
curiosity gap, emotional payoff), keep that DNA, and swap the subject to a DIFFERENT true story that fits Infamazing.
Never copy their topic.

Outliers:
{src}

Reply with JSON only: {{"ideas": [{{"title": "...", "angle": "...", "format": "...", "source": <outlier index>}}]}}"""
    data = llm.ask_json(prompt, model=llm_model, tools=["WebSearch"] if check else None, timeout=900)
    for it in (data.get("ideas", []) if isinstance(data, dict) else []):
        try:
            v = videos[int(it.get("source"))]
            it["src"] = f"outlier: {v['title']} ({v['channel']}, {v['multiple']}x) {v['url']}"
        except (TypeError, ValueError, IndexError):
            pass
    return _save(data, "outliers", log)


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
