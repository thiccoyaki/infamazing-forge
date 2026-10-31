"""Videos/<SEO title>/  ->  <title>.txt (script only), <title>.mp3 (clean VO), vo_raw.mp3, references.txt"""
import json
from pathlib import Path

from . import config as C, db, scripts

def folder_name(v: dict) -> str:
    base = scripts.clean_title(v["title"])
    taken = {x["folder"] for x in db.list_videos() if x["id"] != v["id"] and x["folder"]}
    name, n = base, 2
    while name in taken:
        name, n = f"{base} ({n})", n + 1
    return name


def video_dir(v: dict) -> Path:
    """The video's folder, renamed (with its title-named files) when the title changed. Returns the path."""
    want = folder_name(v)
    d = C.VIDEOS_DIR / want
    old = v.get("folder") or ""
    if old and old != want and (C.VIDEOS_DIR / old).is_dir() and not d.exists():
        (C.VIDEOS_DIR / old).rename(d)
        for ext in (".txt", ".mp3"):
            f = d / f"{old}{ext}"
            if f.exists():
                f.rename(d / f"{want}{ext}")
    d.mkdir(parents=True, exist_ok=True)
    if old != want:
        v["_old_folder"] = old
        db.update_video(v["id"], touch=False, folder=want)
        v["folder"] = want
    return d


def vo_paths(v: dict) -> tuple[Path, Path]:
    d = video_dir(v)
    return d / "vo_raw.mp3", d / f"{v['folder']}.mp3"


def references_txt(v: dict) -> str:
    yt_refs = [r for r in v["refs"] if r.get("kind") == "youtube" or "youtu" in r.get("url", "")]
    web = [r for r in v["refs"] if r not in yt_refs]
    out = [v["title"], ""]
    if v.get("topic"):
        out += [f"Idea: {v['topic']}", ""]
    if yt_refs:
        out += ["YOUTUBE REFERENCES"] + [f"- {r.get('title', '')}\n  {r['url']}" for r in yt_refs] + [""]
    if web:
        out += ["OTHER REFERENCES" if yt_refs else "REFERENCES (no relevant YouTube video found)"] \
            + [f"- {r.get('title', '')}\n  {r['url']}" for r in web] + [""]
    if v.get("facts"):
        out += ["VERIFIED FACTS"] + [f"- {f}" for f in v["facts"]] + [""]
    style = [db.get_ref(i) for i in v.get("style_refs") or []]
    style = [r for r in style if r]
    if style:
        out += ["STYLE REFERENCES USED"] + [f"- {r['title']}" + (f"  {r['url']}" if r["url"] else "") for r in style] + [""]
    if v.get("notes"):
        out += ["NOTES", v["notes"], ""]
    return "\n".join(out).rstrip() + "\n"


def write(vid: int) -> Path:
    v = db.get_video(vid)
    if not v:
        raise KeyError(vid)
    d = video_dir(v)
    name = v["folder"]
    (d / f"{name}.txt").write_text(v["script"], encoding="utf-8")  # script text only
    (d / "references.txt").write_text(references_txt(v), encoding="utf-8")
    meta = {k: v.get(k) for k in ("id", "title", "topic", "format", "voice_name", "vo_seconds", "status", "created_at", "updated_at")}
    meta["chars"] = len(v["script"])
    (d / "video.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    db.mark_packaged(vid)
    from . import drive
    LAST_DRIVE[vid] = drive.mirror_now(d, v.get("_old_folder"))  # local copy into the Drive folder: quick
    return d


LAST_DRIVE: dict[int, str] = {}


def folder_state(v: dict) -> str:
    if not v.get("packaged_at") or not v.get("folder") or not (C.VIDEOS_DIR / v["folder"]).exists():
        return "none"
    return "stale" if v["updated_at"] > v["packaged_at"] else "ok"
