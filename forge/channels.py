"""Find competitor channels from what your audience actually watches next.

Signals, strongest first:
  audience  channels you paste from YouTube Studio > Analytics > Audience > "Channels your audience watches"
  autoplay  the Up next / autoplay list YouTube shows beside your best Shorts (and your competitors' Shorts)
  search    Shorts YouTube returns for the topics of those seeds (backup when the watch page is thin)
Every channel found is scored by how often it shows up, how many different seeds lead to it, and how its recent
Shorts perform. Runs on your PC (watch pages are fetched like a logged-out browser; the rest is the YouTube API)."""
import json
import math
import re
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from . import db, yt

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
WEIGHT = {"audience": 4, "autoplay": 2, "search": 1}
VID = re.compile(r'"videoId":\s*"([\w-]{11})"')


# ---------- seeds

def top_shorts(channel_link: str, n: int, log: list, recent: int = 60) -> list[dict]:
    """A channel's n best-performing Shorts out of its last `recent` uploads."""
    vids = yt.outliers([channel_link], recent, 0, log)  # min multiple 0 = every recent Short, scored
    return sorted(vids, key=lambda v: v["views"], reverse=True)[:n]


def video_ids(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"(?:v=|shorts/|youtu\.be/)([\w-]{11})", text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


# ---------- autoplay / up next

def _initial_data(html: str) -> dict:
    i = html.find("ytInitialData")
    if i < 0:
        return {}
    i = html.find("{", i)
    try:
        return json.JSONDecoder().raw_decode(html[i:])[0]
    except json.JSONDecodeError:
        return {}


def up_next(video_id: str) -> list[str]:
    """Video ids YouTube recommends next to this video (the autoplay / Up next column), as a logged-out viewer."""
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={video_id}&hl=en&gl=US", headers={
        "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+cb; SOCS=CAI"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    data = _initial_data(html)
    side = (((data.get("contents") or {}).get("twoColumnWatchNextResults") or {}).get("secondaryResults")) or {}
    blob = json.dumps(side) if side else ""
    if not blob:  # some layouts put the up-next list elsewhere; fall back to everything except the main video
        blob = json.dumps((data.get("contents") or {}))
    ids = []
    for v in VID.findall(blob):
        if v != video_id and v not in ids:
            ids.append(v)
    return ids[:40]


def _video_channels(ids: list[str]) -> dict[str, tuple[str, str, str]]:
    """video id -> (channel id, channel title, video title), 50 per API call."""
    out = {}
    for i in range(0, len(ids), 50):
        for v in yt.get("videos", {"part": "snippet", "id": ",".join(ids[i:i + 50])}).get("items", []):
            s = v["snippet"]
            out[v["id"]] = (s["channelId"], s["channelTitle"], s["title"])
    return out


# ---------- audience list pasted from YouTube Studio

def resolve_names(lines: list[str], log: list) -> list[tuple[str, str]]:
    out = []
    for ln in lines:
        ln = ln.strip().lstrip("•-*0123456789. \t")
        if not ln:
            continue
        try:
            if re.search(r"UC[\w-]{22}|@[\w.\-]+|youtu", ln):
                out.append(yt.channel_id(ln))
            else:
                items = yt.get("search", {"part": "snippet", "q": ln, "type": "channel", "maxResults": 1}).get("items", [])
                if items:
                    out.append((items[0]["snippet"]["channelId"], items[0]["snippet"]["title"]))
                else:
                    log.append(f"  audience: no channel found for {ln!r}")
        except Exception as e:  # noqa: BLE001
            log.append(f"  audience: {ln!r}: {e}")
    return out


# ---------- main

def find(my_channel: str, seed_links: str, audience: str, competitors: list[str], use_competitors: bool,
         per_channel: int, use_search: bool, log: list) -> dict:
    hits: dict[str, dict] = {}
    exclude = set()

    def hit(cid: str, title: str, kind: str, via: str, sample: str = ""):
        h = hits.setdefault(cid, {"id": cid, "title": title, "score_hits": 0, "counts": defaultdict(int),
                                  "via": set(), "samples": []})
        h["score_hits"] += WEIGHT[kind]
        h["counts"][kind] += 1
        h["via"].add(via)
        if sample and sample not in h["samples"] and len(h["samples"]) < 4:
            h["samples"].append(sample)

    # seeds: your best Shorts, pasted videos, and (optionally) your competitors' best Shorts
    seeds: list[dict] = []
    if my_channel.strip():
        try:
            mine = yt.channel_id(my_channel)
            exclude.add(mine[0])
            log.append(f"seeds: top {per_channel} Shorts of {mine[1]}")
            seeds += top_shorts(my_channel, per_channel, log)
        except Exception as e:  # noqa: BLE001
            log.append(f"your channel: {e}")
    pasted = video_ids(seed_links)
    if pasted:
        info = _video_channels(pasted)
        seeds += [{"id": v, "title": info.get(v, ("", "", v))[2], "channel": info.get(v, ("", "", ""))[1]} for v in pasted]
    if use_competitors:
        for c in competitors[:15]:
            try:
                log.append(f"seeds: top 2 Shorts of {c}")
                seeds += top_shorts(c, 2, log, recent=40)
            except Exception as e:  # noqa: BLE001
                log.append(f"  {c}: {e}")
    for c in competitors:
        try:
            exclude.add(yt.channel_id(c)[0])
        except Exception:  # noqa: BLE001
            pass

    # autoplay
    auto_ids: dict[str, str] = {}
    for n, s in enumerate(seeds, 1):
        try:
            ids = up_next(s["id"])
            log.append(f"[{n}/{len(seeds)}] up next for “{s['title'][:60]}”: {len(ids)} videos")
            for v in ids:
                auto_ids.setdefault(v, s["title"])
        except Exception as e:  # noqa: BLE001
            log.append(f"[{n}/{len(seeds)}] up next failed for {s['id']}: {e}")
        time.sleep(1.5)  # gentle with YouTube
    for vid, (cid, ctitle, vtitle) in _video_channels(list(auto_ids)).items():
        hit(cid, ctitle, "autoplay", auto_ids[vid], vtitle)

    # search backup: Shorts on the same topics as your seeds
    if use_search:
        for s in seeds[:per_channel]:
            q = re.sub(r"[#@]\S+|[^\w\s']", " ", s["title"]).strip()
            if not q:
                continue
            try:
                for it in yt.get("search", {"part": "snippet", "q": q, "type": "video", "videoDuration": "short",
                                            "maxResults": 15}).get("items", []):
                    sn = it["snippet"]
                    hit(sn["channelId"], sn["channelTitle"], "search", s["title"], sn["title"])
            except Exception as e:  # noqa: BLE001
                log.append(f"search failed: {e}")
                break

    # audience list from Studio
    if audience.strip():
        for cid, title in resolve_names(audience.splitlines(), log):
            hit(cid, title, "audience", "your audience watches")

    for c in exclude:
        hits.pop(c, None)
    log.append(f"{len(hits)} channels found, checking their recent Shorts…")
    ranked = sorted(hits.values(), key=lambda h: (h["score_hits"], len(h["via"])), reverse=True)[:40]
    chans = sorted((_enrich(h, log) for h in ranked), key=lambda c: c["score"], reverse=True)
    return {"channels": chans, "seeds": len(seeds)}


def _enrich(h: dict, log: list) -> dict:
    out = {"id": h["id"], "title": h["title"], "url": f"https://www.youtube.com/channel/{h['id']}",
           "counts": dict(h["counts"]), "via": sorted(h["via"])[:5], "seeds": len(h["via"]), "samples": h["samples"],
           "subs": 0, "thumb": "", "median_views": 0, "shorts": 0, "last_days": None}
    try:
        ch = yt.get("channels", {"part": "snippet,statistics", "id": h["id"]}).get("items", [])
        if ch:
            out["subs"] = int(ch[0]["statistics"].get("subscriberCount", 0) or 0)
            out["thumb"] = ((ch[0]["snippet"].get("thumbnails") or {}).get("default") or {}).get("url", "")
            out["handle"] = ch[0]["snippet"].get("customUrl", "")
        pl = yt.get("playlistItems", {"part": "contentDetails", "playlistId": "UU" + h["id"][2:], "maxResults": 30}).get("items", [])
        ids = [p["contentDetails"]["videoId"] for p in pl]
        if ids:
            vids = yt.get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids)}).get("items", [])
            shorts = [v for v in vids if yt._secs(v["contentDetails"].get("duration", "")) <= 180]
            out["shorts"] = len(shorts)
            if shorts:
                out["median_views"] = int(statistics.median(int(v["statistics"].get("viewCount", 0)) for v in shorts))
            if vids:
                last = max(v["snippet"]["publishedAt"] for v in vids)
                out["last_days"] = (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
    except Exception as e:  # noqa: BLE001
        log.append(f"  {h['title']}: {e}")
    # score: audience overlap first, then reach; Shorts-first and active channels rank up
    shorts_share = out["shorts"] / 30 if out["shorts"] else 0
    active = 1.0 if out["last_days"] is not None and out["last_days"] <= 30 else 0.5
    out["score"] = round(h["score_hits"] * (1 + len(h["via"]) / 3) * math.log10(max(out["median_views"], 10))
                         * (0.4 + shorts_share) * active, 1)
    return out


def saved_competitors() -> list[str]:
    return [x.strip() for x in (db.get_setting("competitors", "") or "").splitlines() if x.strip()]
