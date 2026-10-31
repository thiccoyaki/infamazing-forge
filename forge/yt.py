"""YouTube Data API: competitor outliers (same logic as story-forge's concept generator) + reference search."""
import json
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config as C, db


def key() -> str:
    k = C.yt_key() or db.get_setting("yt_api_key", "")
    if not k:
        raise RuntimeError("No YouTube API key: add YT_API_KEY to .env or paste it in Settings")
    return k


def get(endpoint: str, params: dict, retries: int = 3) -> dict:
    q = urllib.parse.urlencode({**params, "key": key()})
    err = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(f"https://www.googleapis.com/youtube/v3/{endpoint}?{q}", timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            err = f"{e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
            if e.code in (401, 403, 404):
                raise RuntimeError(f"YouTube API error {err}")
        except urllib.error.URLError as e:
            err = f"network error: {e.reason}"
        if attempt < retries:
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(err)


def search(query: str, n: int = 3) -> list[dict]:
    if not (C.yt_key() or db.get_setting("yt_api_key", "")):
        return []
    items = get("search", {"part": "snippet", "q": query, "type": "video", "maxResults": n}).get("items", [])
    return [{"title": it["snippet"]["title"], "channel": it["snippet"]["channelTitle"],
             "url": f"https://www.youtube.com/watch?v={it['id']['videoId']}"} for it in items]


def _secs(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def channel_id(link: str) -> tuple[str, str]:
    link = link.strip()
    if m := re.search(r"(UC[\w-]{22})", link):
        cid = m.group(1)
    elif m := re.search(r"@([\w.\-]+)", link):
        items = get("channels", {"part": "id", "forHandle": "@" + m.group(1)}).get("items", [])
        if not items:
            raise ValueError(f"no channel for @{m.group(1)}")
        cid = items[0]["id"]
    elif m := re.search(r"(?:v=|shorts/|youtu\.be/)([\w-]{11})", link):
        items = get("videos", {"part": "snippet", "id": m.group(1)}).get("items", [])
        if not items:
            raise ValueError("video not found")
        cid = items[0]["snippet"]["channelId"]
    else:
        raise ValueError(f"can't read a channel from {link!r}: paste the channel URL, @handle, or one of its videos")
    ch = get("channels", {"part": "snippet", "id": cid}).get("items", [])
    return cid, (ch[0]["snippet"]["title"] if ch else cid)


def outliers(links: list[str], per_channel: int, min_multiple: float, log: list) -> list[dict]:
    """Recent Shorts of each channel whose views are >= min_multiple x that channel's median, fresh ones first."""
    out = []
    for link in links:
        try:
            cid, name = channel_id(link)
        except Exception as e:  # noqa: BLE001
            log.append(f"{link}: {e}")
            continue
        ids, token = [], None
        while len(ids) < per_channel:
            p = {"part": "contentDetails", "playlistId": "UU" + cid[2:], "maxResults": 50}
            if token:
                p["pageToken"] = token
            data = get("playlistItems", p)
            ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                break
        vids = []
        for i in range(0, len(ids[:per_channel]), 50):
            for v in get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])}).get("items", []):
                if _secs(v["contentDetails"].get("duration", "")) > 180:
                    continue
                vids.append({"id": v["id"], "title": v["snippet"]["title"], "channel": name,
                             "url": f"https://www.youtube.com/shorts/{v['id']}",
                             "published": v["snippet"]["publishedAt"], "views": int(v["statistics"].get("viewCount", 0)),
                             "thumb": ((v["snippet"].get("thumbnails") or {}).get("medium") or {}).get("url", "")})
        if len(vids) < 3:
            log.append(f"{name}: only {len(vids)} recent Shorts, skipped")
            continue
        med = statistics.median(v["views"] for v in vids) or 1
        hits = [dict(v, multiple=round(v["views"] / med, 1)) for v in vids if v["views"] / med >= min_multiple]
        out += hits
        log.append(f"{name}: {len(vids)} recent Shorts, median {int(med):,} views, {len(hits)} outlier(s) >= {min_multiple}x")
    now = datetime.now(timezone.utc)
    for v in out:
        age = max(1, (now - datetime.fromisoformat(v["published"].replace("Z", "+00:00"))).days)
        v["age_days"] = age
        v["score"] = round(v["multiple"] / (1 + age / 60), 2)
    return sorted(out, key=lambda v: v["score"], reverse=True)
