"""Competitor scout (method: COMPETITOR_SCOUT.md).

Finds the channels that compete for your viewers by reading YouTube's "Up next" sidebar next to your Shorts (real
audience overlap), one hop out from your closest neighbours, then measures each with the Data API, keeps active
Shorts-first channels, drops brand clones, and has Claude score how well each fits your channel."""
import datetime as dt
import json
import math
import re
import statistics
import time
import urllib.request

from . import db, llm, yt

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
SEEDS, HOP_FROM, HOP_SEEDS, MEASURE_MAX, CACHE_DAYS = 15, 3, 5, 45, 3


def settings() -> dict:
    return {"channel": db.get_setting("my_channel", "") or "https://www.youtube.com/@infamazing", "gl": "US", "pinned": [],
            **db.get_setting("scout", {})}


def save_settings(b: dict) -> dict:
    s = settings()
    for k in ("channel", "gl", "pinned"):
        if k in b and b[k] is not None:
            s[k] = b[k]
    db.set_setting("scout", s)
    return s


# ---------- Up next (read from the user's own PC, plain HTTP)


def _watch_html(vid: str, gl: str) -> str:
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={vid}&hl=en&gl={gl}", headers={
        "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _initial_data(html: str) -> dict:
    m = re.search(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*", html)
    if not m:
        return {}
    try:
        return json.JSONDecoder().raw_decode(html[m.end():])[0]
    except ValueError:
        return {}


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _text(o) -> str:
    if isinstance(o, dict):
        if "simpleText" in o:
            return o["simpleText"]
        if "runs" in o:
            return "".join(r.get("text", "") for r in o["runs"])
        if "content" in o and isinstance(o["content"], str):
            return o["content"]
    return ""


def parse_up_next(data: dict) -> list[dict]:
    """[{channel_id, channel, video_id, title}] from a watch page's ytInitialData. Works on both the older
    compactVideoRenderer and the newer lockupViewModel layouts: per item, the first UC… browseId is the channel."""
    sec = next((d["secondaryResults"] for d in _walk(data) if "secondaryResults" in d), None)
    if sec is None:
        return []
    out, seen = [], set()
    for d in _walk(sec):
        for key in ("compactVideoRenderer", "lockupViewModel", "reelItemRenderer"):
            item = d.get(key)
            if not isinstance(item, dict):
                continue
            nodes = list(_walk(item))
            cid = next((n["browseId"] for n in nodes if str(n.get("browseId", "")).startswith("UC")), "")
            vid = item.get("videoId") or item.get("contentId") or next((n["videoId"] for n in nodes if "videoId" in n), "")
            if not cid or (cid, vid) in seen:
                continue
            seen.add((cid, vid))
            title = _text(item.get("title", {})) or next(
                (_text(n["title"]) for n in nodes if isinstance(n.get("title"), dict) and _text(n["title"])), "")
            name = _text(item.get("shortBylineText", {})) or _text(item.get("longBylineText", {}))
            if not name:  # lockupViewModel: first metadata text that isn't the title or a view count
                name = next((t for t in (_text(n.get("text")) for n in nodes if isinstance(n.get("text"), dict))
                             if t and t != title and not re.search(r"\bviews?\b|ago$|^\d", t)), "")
            out.append({"channel_id": cid, "channel": name, "video_id": vid, "title": title})
    return out


def up_next(vid: str, gl: str) -> list[dict]:
    return parse_up_next(_initial_data(_watch_html(vid, gl)))


# ---------- measuring channels (YouTube Data API)


def _iso_secs(d: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    return (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0


def _age_factor(days: float) -> float:
    """Share of its eventual views a Short usually has at this age (rough; Shorts get most views in days)."""
    return 0.3 if days < 1 else 0.6 if days < 3 else 0.85 if days < 7 else 1.0


def recent_shorts(channel_id: str, n: int = 30) -> tuple[list[dict], int]:
    """(newest `n` Shorts with views/age, total Shorts on the channel). Uses the Shorts-only uploads playlist."""
    try:
        pl = yt.get("playlistItems", {"part": "contentDetails", "maxResults": min(n, 50),
                                             "playlistId": "UUSH" + channel_id[2:]})
    except Exception:  # noqa: BLE001 - no Shorts playlist = no Shorts
        return [], 0
    ids = [it["contentDetails"]["videoId"] for it in pl.get("items", [])]
    total = int((pl.get("pageInfo") or {}).get("totalResults") or len(ids))
    if not ids:
        return [], total
    vids = yt.get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)}).get("items", [])
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for v in vids:
        pub = dt.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        out.append({"id": v["id"], "title": v["snippet"]["title"], "views": int(v["statistics"].get("viewCount", 0)),
                    "age_days": round((now - pub).total_seconds() / 86400, 1), "published": v["snippet"]["publishedAt"],
                    "secs": _iso_secs(v["contentDetails"].get("duration", "")),
                    "thumb": ((v["snippet"].get("thumbnails") or {}).get("medium") or {}).get("url", "")})
    return sorted(out, key=lambda s: s["published"], reverse=True), total


def measure(channel_ids: list[str]) -> dict[str, dict]:
    info = {}
    for i in range(0, len(channel_ids), 50):
        for c in yt.get("channels", {"part": "snippet,statistics", "id": ",".join(channel_ids[i:i + 50])}).get("items", []):
            info[c["id"]] = c
    out = {}
    now = dt.datetime.now(dt.timezone.utc)
    for cid in channel_ids:
        c = info.get(cid)
        if not c:
            continue
        shorts, n_shorts = recent_shorts(cid)
        st, sn = c.get("statistics", {}), c["snippet"]
        uploads = int(st.get("videoCount") or 0) or n_shorts or 1
        base = [s["views"] for s in shorts if s["age_days"] >= 3] or [s["views"] for s in shorts]
        usual = int(statistics.median(base)) if base else 0
        for s in shorts:
            s["x"] = round(s["views"] / max(1, usual * _age_factor(s["age_days"])), 1) if usual else 0
        half = len(shorts) // 2
        newer, older = [s["views"] for s in shorts[:half]], [s["views"] for s in shorts[half:]]
        started = dt.datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        out[cid] = {
            "id": cid, "name": sn.get("title", ""), "handle": sn.get("customUrl", ""), "country": sn.get("country", ""),
            "description": (sn.get("description") or "")[:300],
            "thumb": ((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""),
            "subs": int(st.get("subscriberCount") or 0), "started_days": (now - started).days,
            "n_shorts": n_shorts, "shorts_share": round(min(1.0, n_shorts / uploads), 2),
            "usual": usual, "best": max((s["views"] for s in shorts), default=0),
            "per_week": round(sum(1 for s in shorts if s["age_days"] <= 30) / 30 * 7, 1),
            "last_short_days": shorts[0]["age_days"] if shorts else None,
            "rising": round(statistics.median(newer) / max(1, statistics.median(older)), 2) if newer and older else None,
            "hits": sorted([s for s in shorts if s["x"] >= 3], key=lambda s: -s["x"])[:4],
            "recent": shorts[:6], "titles": [s["title"] for s in shorts[:8]],
        }
    return out


# ---------- fit check (one Claude call for the batch)

STOP = set("the a an of to and in on for with is i my you your me this that it at how why what when vs from".split())


def _words(titles: list[str]) -> set:
    return {w for t in titles for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in STOP}


def fit_check(me: dict, cands: list[dict], llm_model: str, log: list) -> None:
    """Sets c['fit'] (0-10) and c['why'] on each candidate."""
    if not cands:
        return
    src = "\n".join(f"[{i}] {c['name']} ({c.get('country') or '?'}, {c['subs']:,} subs): " + " | ".join(c["titles"][:6])
                    for i, c in enumerate(cands))
    prompt = f"""You judge which YouTube Shorts channels really compete with this creator for the same viewers.

THE CREATOR: {me.get('name')} ({me.get('country') or '?'})
About: {me.get('description') or '-'}
Recent Shorts: {' | '.join(me.get('titles', [])[:8])}

CANDIDATES (name, country, subscribers: recent Shorts titles):
{src}

For each candidate give fit 0-10: same kind of Short (format, genre, how it's told) for the same audience.
10 = the same kind of channel; 5 = related but a different format; 0 = unrelated, or a language/region that doesn't fit
this creator (e.g. local TV, news, music, another language's channel picked up from the viewer's own location).
Return JSON only: {{"fits": [{{"i": <index>, "fit": <0-10>, "why": "one short line, e.g. 'food brand trivia Shorts, similar hooks'"}}]}}"""
    try:
        data = llm.ask_json(prompt, model=llm_model)
        for f in (data.get("fits") if isinstance(data, dict) else None) or []:
            try:
                c = cands[int(f["i"])]
                c["fit"], c["why"] = max(0, min(10, int(f.get("fit", 0)))), str(f.get("why", ""))
            except (KeyError, ValueError, IndexError, TypeError):
                continue
    except Exception as e:  # noqa: BLE001 - fall back to shared topic words
        log.append(f"fit check failed ({e}); using shared title words instead")
    mine = _words(me.get("titles", []))
    for c in cands:
        if "fit" not in c:
            j = len(mine & _words(c["titles"])) / max(1, len(mine | _words(c["titles"])))
            c["fit"], c["why"] = min(10, round(j * 30)), "shared title words (no AI check)"


# ---------- the scan


def _seed_videos(mode: str, query: str, log: list) -> tuple[list[str], dict | None]:
    """(seed Short ids, the creator channel being compared against, or None for a niche)."""
    if mode == "niche":
        after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items = yt.get("search", {"part": "id", "type": "video", "videoDuration": "short", "q": query,
                                         "order": "viewCount", "publishedAfter": after, "maxResults": SEEDS}).get("items", [])
        return [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")], None
    link = query or settings()["channel"]
    cid, name = yt.channel_id(link)
    me = measure([cid]).get(cid)
    if not me:
        raise ValueError(f"couldn't read {link}")
    log.append(f"{me['name']}: usual {me['usual']:,} views over {len(me['recent'])} recent Shorts")
    shorts, _ = recent_shorts(cid, SEEDS)
    return [s["id"] for s in shorts], me


def _overlap(seeds: list[str], gl: str, log: list, label: str) -> tuple[dict, int]:
    """{channel_id: {name, appearances, examples}} counted once per seed video; and how many seeds were read."""
    found, read = {}, 0
    for i, vid in enumerate(seeds):
        try:
            items = up_next(vid, gl)
        except Exception as e:  # noqa: BLE001 - one page failing shouldn't stop the scan
            log.append(f"{label} {i + 1}/{len(seeds)}: couldn't read Up next ({str(e)[:80]})")
            continue
        read += 1
        for cid in {it["channel_id"] for it in items}:
            it = next(x for x in items if x["channel_id"] == cid)
            f = found.setdefault(cid, {"name": it["channel"], "appearances": 0, "examples": []})
            f["appearances"] += 1
            f["name"] = f["name"] or it["channel"]
            if len(f["examples"]) < 3 and it["video_id"]:
                f["examples"].append({"id": it["video_id"], "title": it["title"]})
        log.append(f"{label} {i + 1}/{len(seeds)}: {len(items)} recommendations")
        time.sleep(1.5)
    return found, read


def _closeness(c: dict, me: dict | None) -> int:
    pts = {"close": 4, "medium": 3, "far": 2, "hop 2": 1.5, "pinned": 2.5}.get(c["grade"], 1.5) + c["fit"] * 0.4
    if me:
        j = len(_words(me["titles"]) & _words(c["titles"])) / max(1, len(_words(me["titles"]) | _words(c["titles"])))
        pts += min(2.0, j * 10)
        if me.get("usual") and c.get("usual"):
            pts += max(0.0, 1 - abs(math.log10(max(1, c["usual"]) / max(1, me["usual"]))) / 2)
    return max(1, min(10, round(pts)))


def _verdict(kept: list[dict]) -> dict:
    if not kept:
        return {"verdict": "unknown"}
    small_win = [c["name"] for c in kept if c["subs"] < 100_000 and c["hits"]]
    new_win = [c["name"] for c in kept if c["started_days"] <= 183 and c["hits"]]
    hot = [h | {"channel": c["name"]} for c in kept for h in c["hits"] if h["age_days"] <= 7]
    winners = [c for c in kept if c["hits"]]
    verdict = "open" if (small_win or new_win) else "competitive" if winners else "crowded"
    return {"verdict": verdict, "usual": int(statistics.median([c["usual"] for c in kept])),
            "small_winning": small_win, "new_winning": new_win, "hot_now": len(hot), "channels": len(kept)}


def scan(mode: str, query: str, llm_model: str, log: list, refresh: bool = False) -> dict:
    key = f"scout:{mode}:{(query or '').strip().lower()}"
    cached = db.get_setting(key)
    if cached and not refresh and time.time() - cached.get("at", 0) < CACHE_DAYS * 86400:
        log.append("using the scan from " + dt.datetime.fromtimestamp(cached["at"]).strftime("%b %d, %I:%M %p"))
        return cached
    s = settings()
    seeds, me = _seed_videos(mode, query, log)
    if not seeds:
        raise ValueError("no Shorts to start from")
    found, read = _overlap(seeds, s["gl"], log, "Up next")
    if not read:
        raise RuntimeError("couldn't read YouTube's Up next on any video (blocked or offline?)")
    if me:
        found.pop(me["id"], None)
    for f in found.values():
        share = f["appearances"] / read
        f["grade"], f["hop"] = ("close" if share >= 0.5 else "medium" if share >= 0.2 else "far"), 1
    closest = sorted(found, key=lambda c: -found[c]["appearances"])[:HOP_FROM]
    if closest:  # one hop out: who sits next to your closest neighbours
        hop_seeds = []
        for cid in closest:
            hop_seeds += [x["id"] for x in recent_shorts(cid, HOP_SEEDS)[0]]
        hop, _ = _overlap(hop_seeds, s["gl"], log, "one hop out")
        for cid, f in hop.items():
            if cid not in found and (not me or cid != me["id"]):
                found[cid] = {**f, "grade": "hop 2", "hop": 2}
    seen = len(found)
    pinned = [p for p in s.get("pinned", []) if p]
    order = sorted(found, key=lambda c: (found[c]["hop"], -found[c]["appearances"]))[:MEASURE_MAX]
    order += [p for p in pinned if p not in order]
    log.append(f"{seen} channels seen; measuring {len(order)}…")
    measured = measure(order)
    cands, creators = [], 0
    myname = (me or {}).get("name", "").lower()
    for cid, m in measured.items():
        m.update({k: v for k, v in found.get(cid, {}).items() if k in ("appearances", "examples", "grade", "hop")})
        m.setdefault("grade", "pinned")
        m["pinned"] = cid in pinned
        active = m["shorts_share"] >= 0.5 and m["last_short_days"] is not None and m["last_short_days"] <= 30
        clone = bool(myname) and len(myname) >= 4 and myname in m["name"].lower()
        if clone:
            continue
        if active:
            creators += 1
        if active or m["pinned"]:
            cands.append(m)
    fit_check(me or {"name": query, "titles": [query]}, cands, llm_model, log)
    kept = [c for c in cands if c["fit"] >= 5 or c["pinned"]]
    for c in kept:
        c["closeness"] = _closeness(c, me)
    kept.sort(key=lambda c: (-c["closeness"], -c["fit"]))
    feed = sorted([h | {"channel": c["name"], "channel_id": c["id"]} for c in kept for h in c["recent"]
                   if h["age_days"] <= 14 and h["x"] >= 1.5], key=lambda h: -h["x"])[:30]
    res = {"at": time.time(), "mode": mode, "query": query, "gl": s["gl"], "me": me,
           "funnel": {"seeds": len(seeds), "read": read, "seen": seen, "measured": len(measured),
                      "shorts_creators": creators, "kept": len(kept)},
           "verdict": _verdict(kept), "channels": kept, "feed": feed,
           "dropped": [{"name": c["name"], "fit": c["fit"], "why": c["why"]} for c in cands if c not in kept][:20]}
    db.set_setting(key, res)
    db.set_setting("scout_last", key)
    log.append(f"kept {len(kept)} competitors · niche: {res['verdict']['verdict']}")
    return res


def last() -> dict | None:
    k = db.get_setting("scout_last")
    return db.get_setting(k) if k else None


def pin(link: str, on: bool = True) -> dict:
    cid = link if re.fullmatch(r"UC[\w-]{22}", link or "") else yt.channel_id(link)[0]
    s = settings()
    s["pinned"] = [p for p in s["pinned"] if p != cid] + ([cid] if on else [])
    db.set_setting("scout", s)
    return s
