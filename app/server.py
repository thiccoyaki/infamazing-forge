"""infamazing-forge local server: python -m app  ->  http://localhost:8778"""
import csv
import io
import threading
import traceback
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from forge import channels, config as C, db, drive, ideas, llm, output, review, scout, scripts, vo, yt, ytref

app = FastAPI(title="infamazing-forge")


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


C.load_env()
db.init()
if not db.get_setting("kalemod_imported"):  # first start: take Kalemod's voice + VO settings from story-forge
    try:
        vo.import_kalemod()
        C.load_env()
    except Exception:  # noqa: BLE001
        traceback.print_exc()

# ---------- background tasks

TASKS: dict[str, dict] = {}


def run_task(fn) -> str:
    tid = uuid.uuid4().hex[:10]
    TASKS[tid] = {"status": "running", "log": [], "result": None, "error": ""}

    def go():
        try:
            TASKS[tid]["result"] = fn(TASKS[tid]["log"])
            TASKS[tid]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            TASKS[tid]["error"] = str(e)
            TASKS[tid]["log"].append(traceback.format_exc()[-800:])
            TASKS[tid]["status"] = "error"

    threading.Thread(target=go, daemon=True).start()
    return tid


@app.get("/api/task/{tid}")
def task_status(tid: str):
    if tid not in TASKS:
        raise HTTPException(404, "no such task")
    return TASKS[tid]


# ---------- health / settings

@app.get("/api/health")
def health():
    ok, msg = True, ""
    try:
        llm.find_claude()
    except llm.ClaudeError as e:
        ok, msg = False, str(e)
    try:
        C.elevenlabs_key()
        el = True
    except RuntimeError:
        el = False
    return {"claude": ok, "claude_msg": msg, "elevenlabs": el, "ffmpeg": vo.have_ffmpeg(),
            "youtube": bool(C.yt_key() or db.get_setting("yt_api_key", "")), "drive": drive.status()["ok"],
            "llm_models": C.LLM_MODELS, "default_llm": db.get_setting("default_llm", C.DEFAULT_LLM_MODEL)}


SETTING_KEYS = ("my_channel", "drive_dir", "drive_url", "default_format", "default_llm", "yt_api_key", "competitors", "audience_channels",
                "default_voice", "voice_settings", "deadair")


@app.get("/api/settings")
def get_settings():
    s = {k: db.get_setting(k) for k in SETTING_KEYS}
    s.update(vo.voice_defaults())
    s["drive"] = drive.status()
    s["story_forge_dir"] = str(C.story_forge_dir())
    s["kalemod_imported"] = db.get_setting("kalemod_imported")
    s["global"] = scripts.global_rules()
    return s


@app.put("/api/settings")
def put_settings(body: dict):
    for k in SETTING_KEYS:
        if k in body:
            db.set_setting(k, drive.clean_path(body[k]) if k == "drive_dir" else body[k])
    if "drive_dir" in body and drive.status()["ok"]:  # new Drive folder: copy everything made so far
        run_task(drive.mirror_all)
    if "global" in body:
        (C.FORMATS_DIR / "_global.md").write_text(body["global"], encoding="utf-8")
    return {"ok": True}


@app.post("/api/settings/import-kalemod")
def import_kalemod():
    r = vo.import_kalemod()
    C.load_env()
    if not r["found"]:
        raise HTTPException(400, f"story-forge's forge.db not found in {r['dir']}. Set STORY_FORGE_DIR in .env.")
    return r


@app.post("/api/drive/detect")
def drive_detect():
    return {"dir": drive.detect(force=True)}


@app.post("/api/drive/sync-all")
def drive_sync_all():
    return {"task": run_task(drive.mirror_all)}


# ---------- formats

@app.get("/api/formats")
def formats():
    return {"formats": scripts.list_formats(), "default": scripts.default_format()}


@app.get("/api/formats/{fid}")
def get_format(fid: str):
    try:
        return {"id": fid, "body": scripts.read_format(fid)}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e))


class Body(BaseModel):
    body: str


@app.put("/api/formats/{fid}")
def put_format(fid: str, b: Body):
    try:
        scripts.write_format(fid, b.body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/formats/{fid}")
def delete_format(fid: str):
    p = scripts.format_path(fid)
    if p.exists():
        p.unlink()
    return {"ok": True}


class FormatFromRefs(BaseModel):
    id: str
    name: str
    ref_ids: list[int]
    notes: str = ""
    llm_model: str | None = None


@app.post("/api/formats/from-refs")
def format_from_refs(b: FormatFromRefs):
    """New format later on: Claude reverse-engineers a format file from picked references."""
    def work(log):
        log.append(f"studying {len(b.ref_ids)} reference(s)…")
        lens = [len(r["body"]) for r in (db.get_ref(i) for i in b.ref_ids) if r]
        prompt = (f"Below are YouTube Shorts scripts that share one format. Write a reusable FORMAT spec file for it, "
                  f"in the same shape as this example spec:\n\n{scripts.read_format(scripts.default_format())}\n\n"
                  f"Line 1: '# {b.name}'. Line 2: a one-line description. Line 3: 'chars: MIN-MAX' "
                  f"(these scripts are {min(lens, default=700)}-{max(lens, default=900)} characters). Then the numbered "
                  f"structure with the exact recurring phrases, the CTA pattern and where it sits, the ending pattern, "
                  f"and one of the scripts as the example. Reply with ONLY the file content.\n"
                  + (f"Notes from the creator: {b.notes}\n" if b.notes else "") + "\n" + scripts.style_block(b.ref_ids))
        body = llm.ask(prompt, model=b.llm_model)
        scripts.write_format(b.id, body.strip().strip("`") + "\n")
        log.append(f"format saved: formats/{b.id}.md")
        return {"id": b.id}
    return {"task": run_task(work)}


# ---------- ideas

@app.get("/api/ideas")
def list_ideas():
    return {"ideas": db.list_ideas()}


class AddIdeas(BaseModel):
    lines: str
    format: str = ""


@app.post("/api/ideas")
def add_ideas(b: AddIdeas):
    n = 0
    for ln in b.lines.splitlines():
        if ln.strip():
            title, _, angle = ln.partition("|")
            db.add_idea(title.strip(), angle.strip(), b.format or scripts.default_format(), "manual")
            n += 1
    return {"added": n}


@app.put("/api/ideas/{iid}")
def update_idea(iid: int, body: dict):
    db.update_idea(iid, **body)
    return {"ok": True}


@app.delete("/api/ideas/{iid}")
def delete_idea(iid: int):
    db.delete_idea(iid)
    return {"ok": True}


class Brainstorm(BaseModel):
    count: int = 10
    direction: str = ""
    check: bool = False
    llm_model: str | None = None


@app.post("/api/ideas/brainstorm")
def brainstorm(b: Brainstorm):
    return {"task": run_task(lambda log: {"ideas": ideas.brainstorm(b.count, b.direction, b.check, b.llm_model, log)})}


class Outliers(BaseModel):
    links: list[str]
    per_channel: int = 50
    min_multiple: float = 2.0


@app.post("/api/ideas/outliers")
def find_outliers(b: Outliers):
    db.set_setting("competitors", "\n".join(b.links))
    return {"task": run_task(lambda log: {"videos": yt.outliers(b.links, b.per_channel, b.min_multiple, log)})}


class Winners(BaseModel):
    my_channel: str
    recent: int = 50
    min_multiple: float = 1.5


@app.post("/api/ideas/winners")
def my_winners(b: Winners):
    """Your own latest Shorts that beat your channel median: the starting point for follow-up ideas."""
    db.set_setting("my_channel", b.my_channel)
    return {"task": run_task(lambda log: {"videos": yt.outliers([b.my_channel], b.recent, b.min_multiple, log)})}


class Bend(BaseModel):
    videos: list[dict]
    mode: str = "adapt"           # adapt = their topic in our structure, bend = new topic with their hook DNA
    count: int = 10
    direction: str = ""
    check: bool = False
    llm_model: str | None = None


@app.post("/api/ideas/bend")
def bend(b: Bend):
    return {"task": run_task(lambda log: {"ideas": ideas.from_outliers(b.videos, b.count, b.direction, b.check, b.llm_model, log, b.mode)})}


class FindChannels(BaseModel):
    my_channel: str = ""
    seed_links: str = ""          # extra seed videos (yours or ones your viewers love)
    audience: str = ""            # pasted from Studio > Audience > "Channels your audience watches"
    use_competitors: bool = True  # snowball: also look at what plays after your competitors' best Shorts
    per_channel: int = 8
    use_search: bool = True
    scripts_n: int = 10           # start from your latest N scripts (0 = off)


@app.post("/api/channels/find")
def find_channels(b: FindChannels):
    if b.my_channel:
        db.set_setting("my_channel", b.my_channel)
    if b.audience:
        db.set_setting("audience_channels", b.audience)
    return {"task": run_task(lambda log: channels.find(b.my_channel, b.seed_links, b.audience, channels.saved_competitors(),
                                                       b.use_competitors, b.per_channel, b.use_search, log, b.scripts_n))}


class AddCompetitors(BaseModel):
    links: list[str]


@app.post("/api/channels/add")
def add_competitors(b: AddCompetitors):
    have = channels.saved_competitors()
    new = [x for x in b.links if x not in have]
    db.set_setting("competitors", "\n".join(have + new))
    return {"added": len(new), "competitors": have + new}


# ---------- competitor scout (COMPETITOR_SCOUT.md)

@app.get("/api/scout")
def scout_state():
    return {"settings": scout.settings(), "last": scout.last()}


@app.put("/api/scout/settings")
def scout_settings(body: dict):
    return scout.save_settings(body)


class ScanBody(BaseModel):
    mode: str = "me"  # me | like | niche
    query: str = ""
    refresh: bool = False
    llm_model: str | None = None


@app.post("/api/scout/scan")
def scout_scan(b: ScanBody):
    if b.mode in ("like", "niche") and not b.query.strip():
        raise HTTPException(400, "paste a channel link" if b.mode == "like" else "type a niche")
    return {"task": run_task(lambda log: scout.scan(b.mode, b.query.strip(), b.llm_model, log, b.refresh))}


class PinBody(BaseModel):
    link: str
    on: bool = True


@app.post("/api/scout/pin")
def scout_pin(b: PinBody):
    return scout.pin(b.link, b.on)


# ---------- videos

def _view(v: dict) -> dict:
    v["chars"] = len(v["script"])
    v["est_seconds"] = scripts.est_seconds(v["script"], v["voice_id"])
    v["folder_state"] = output.folder_state(v)
    v["vo_stale"] = bool(v["vo_seconds"]) and v["vo_hash"] != _hash(v["script"])
    return v


def _hash(t: str) -> str:
    import hashlib
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


@app.get("/api/videos")
def list_videos():
    return {"videos": [_view(v) for v in db.list_videos()], "statuses": STATUSES}


@app.get("/api/videos/{vid}")
def get_video(vid: int):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    return _view(v)


class Generate(BaseModel):
    idea_ids: list[int] = []
    topics: str = ""              # one per line, "title | angle"
    format: str = ""              # "" = each idea's own format
    ref_ids: list[int] = []       # [] = auto (latest past scripts of that format)
    notes: str = ""
    llm_model: str | None = None
    make_vo: bool = False
    edit: bool = True             # editor pass: AI-tell fix, blind line-up / rubric, keep-only-if-better rewrites


@app.post("/api/videos/generate")
def generate(b: Generate):
    jobs = [(db.get_idea(i), None) for i in b.idea_ids]
    jobs = [(i["title"], i["angle"], b.format or i["format"], i["id"]) for i, _ in jobs if i]
    for ln in b.topics.splitlines():
        if ln.strip():
            t, _, a = ln.partition("|")
            jobs.append((t.strip(), a.strip(), b.format or scripts.default_format(), None))
    if not jobs:
        raise HTTPException(400, "pick ideas or type a topic")

    def work(log):
        made = []
        for n, (topic, angle, fmt, iid) in enumerate(jobs, 1):
            log.append(f"[{n}/{len(jobs)}] {topic}")
            try:
                g = scripts.generate(topic, angle, fmt or scripts.default_format(), b.ref_ids, b.notes, b.llm_model, log, b.edit)
                vid = db.create_video(title=g["title"], topic=f"{topic}" + (f" | {angle}" if angle else ""), format=fmt,
                                      script=g["script"], refs=g["refs"], facts=g["facts"], style_refs=g["style_refs"],
                                      notes=g["notes"], review=g["review"])
                if iid:
                    db.update_idea(iid, status="used", video_id=vid)
                d = output.write(vid)
                log.append(f"  saved: {d} ({len(g['script'])} chars, {len(g['refs'])} refs)")
                log.append("  " + output.LAST_DRIVE.get(vid, ""))
                if b.make_vo:
                    try:
                        _do_vo(vid, None, log)
                    except Exception as e:  # noqa: BLE001
                        log.append(f"  VO FAILED: {e}")
                made.append(vid)
            except Exception as e:  # noqa: BLE001 - one bad topic doesn't stop the batch
                log.append(f"  failed: {e}")
        return {"ids": made}
    return {"task": run_task(work)}


@app.put("/api/videos/{vid}")
def update_video(vid: int, body: dict):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    allowed = {k: body[k] for k in ("title", "script", "format", "refs", "facts", "notes", "topic") if k in body}
    if "title" in allowed:
        allowed["title"] = " ".join(str(allowed["title"]).split())[:100] or v["title"]
    db.update_video(vid, **allowed)
    d = output.write(vid)
    return {"ok": True, "folder": d.name, "drive": output.LAST_DRIVE.get(vid, "")}


@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    db.delete_video(vid)  # the folder on disk / Drive is kept
    return {"ok": True}


class Revise(BaseModel):
    instruction: str
    research: bool = False
    llm_model: str | None = None


@app.post("/api/videos/{vid}/revise")
def revise(vid: int, b: Revise):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)

    def work(log):
        new = scripts.revise(v, b.instruction, b.llm_model, log, b.research)
        db.update_video(vid, script=new)
        output.write(vid)
        return {"chars": len(new)}
    return {"task": run_task(work)}


@app.post("/api/videos/{vid}/review")
def review_video(vid: int, body: dict):
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)

    def work(log):
        ref_ids = v["style_refs"] or scripts.auto_refs(v["format"])
        new, rep = review.review(v["script"], v["format"], ref_ids, v["facts"], body.get("llm_model"), log, v["title"])
        db.update_video(vid, script=new, review=rep)
        output.write(vid)
        return rep
    return {"task": run_task(work)}


class VOBody(BaseModel):
    voice_id: str | None = None
    voice_name: str = ""
    settings: dict | None = None
    deadair: dict | None = None


def _do_vo(vid: int, b: VOBody | None, log: list) -> dict:
    v = db.get_video(vid)
    d = vo.voice_defaults()
    voice = {"voice_id": (b and b.voice_id) or d["voice"].get("voice_id"),
             "voice_name": (b and b.voice_name) or d["voice"].get("voice_name", "")}
    if not voice["voice_id"]:
        raise RuntimeError("No voice set. Import Kalemod settings in Settings, or pick a voice in the VO panel.")
    settings = {**d["settings"], **((b and b.settings) or {})}
    dead = {**d["deadair"], **((b and b.deadair) or {})}
    raw, clean = output.vo_paths(v)
    log.append(f"VO: {v['title']} ({voice['voice_name'] or voice['voice_id']}, speed {dead.get('speed')})")
    raw_s, clean_s = vo.make(v["script"], raw, clean, voice["voice_id"], settings, dead, log)
    pace = db.record_pace(voice["voice_id"], voice["voice_name"], len(v["script"]), clean_s)
    db.update_video(vid, voice_id=voice["voice_id"], voice_name=voice["voice_name"], vo_seconds=round(clean_s, 1),
                    vo_raw_seconds=round(raw_s, 1), vo_hash=_hash(v["script"]),
                    status="vo" if v["status"] == "draft" else v["status"])
    if b:  # settings changed in the VO panel become the new defaults
        db.set_setting("default_voice", voice)
        db.set_setting("voice_settings", settings)
        db.set_setting("deadair", dead)
    output.write(vid)
    log.append(output.LAST_DRIVE.get(vid, ""))
    return {"seconds": clean_s, "pace": pace}


@app.post("/api/videos/{vid}/vo")
def make_vo(vid: int, b: VOBody):
    if not db.get_video(vid):
        raise HTTPException(404)
    return {"task": run_task(lambda log: _do_vo(vid, b, log))}


class Ids(BaseModel):
    ids: list[int]


@app.post("/api/videos/vo-batch")
def vo_batch(b: Ids):
    def work(log):
        done = 0
        for vid in b.ids:
            try:
                _do_vo(vid, None, log)
                done += 1
            except Exception as e:  # noqa: BLE001
                log.append(f"  failed: {e}")
        return {"done": done}
    return {"task": run_task(work)}


STATUSES = ["draft", "vo", "in progress", "live"]  # vo = VO ready


class StatusBody(BaseModel):
    status: str


@app.put("/api/videos/{vid}/status")
def set_status(vid: int, b: StatusBody):
    """Live = published: the script joins the reference library as a past script, so future scripts learn from it."""
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404)
    if b.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    db.update_video(vid, touch=False, status=b.status)
    if b.status == "live":
        db.add_ref(v["title"], v["script"], format=v["format"], source="past", video_id=vid)
    output.write(vid)
    return {"ok": True}


@app.get("/api/videos/{vid}/audio/{which}")
def audio(vid: int, which: str):
    v = db.get_video(vid)
    if not v or not v["folder"]:
        raise HTTPException(404)
    d = C.VIDEOS_DIR / v["folder"]
    p = d / ("vo_raw.mp3" if which == "raw" else f"{v['folder']}.mp3")
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg")


@app.post("/api/videos/{vid}/open")
def open_folder(vid: int):
    import os
    v = db.get_video(vid)
    if v and v["folder"] and os.name == "nt":
        os.startfile(C.VIDEOS_DIR / v["folder"])  # noqa: S606 - local tool, opens Explorer
    return {"ok": True}


# ---------- reference library

@app.get("/api/refs")
def refs():
    return {"refs": db.list_refs()}


class AddRef(BaseModel):
    title: str
    body: str
    format: str = ""
    url: str = ""


@app.post("/api/refs")
def add_ref(b: AddRef):
    return {"id": db.add_ref(b.title.strip() or b.body[:60], b.body.strip(), format=b.format, url=b.url, source="paste")}


@app.put("/api/refs/{rid}")
def update_ref(rid: int, body: dict):
    f = {k: body[k] for k in ("title", "body", "format") if k in body}
    with db.conn() as c:
        c.execute(f"UPDATE refs SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), rid))
    return {"ok": True}


@app.delete("/api/refs/{rid}")
def delete_ref(rid: int):
    db.delete_ref(rid)
    return {"ok": True}


class Links(BaseModel):
    urls: list[str]
    format: str = ""
    tidy: bool = True
    llm_model: str | None = None


@app.post("/api/refs/links")
def refs_from_links(b: Links):
    urls = [u.strip() for u in b.urls if u.strip()]
    return {"task": run_task(lambda log: ytref.add_from_links(urls, b.format, b.tidy, b.llm_model, log))}


class CSVBody(BaseModel):
    text: str
    format: str = ""


@app.post("/api/refs/csv")
def refs_from_csv(b: CSVBody):
    """Past scripts from a CSV export (e.g. infamazing_scripts.csv): finds the title + script columns by name."""
    rows = list(csv.DictReader(io.StringIO(b.text.lstrip("﻿"))))
    if not rows:
        raise HTTPException(400, "empty CSV")
    cols = list(rows[0].keys())
    pick = lambda *names: next((c for c in cols for n in names if c and n in c.lower()), None)  # noqa: E731
    tcol, scol = pick("title", "name", "topic"), pick("script", "body", "text", "transcript", "content")
    ucol = pick("url", "link")
    if not scol:
        raise HTTPException(400, f"no script column found in: {', '.join(cols)}")
    have = {r["body"][:200] for r in db.list_refs()}
    n = 0
    for r in rows:
        body = (r.get(scol) or "").strip()
        if len(body) < 40 or body[:200] in have:
            continue
        db.add_ref((r.get(tcol) or body[:60]).strip(), body, format=b.format, source="csv", url=(r.get(ucol) or "").strip() if ucol else "")
        n += 1
    return {"added": n, "title_col": tcol, "script_col": scol}


# ---------- VO

@app.get("/api/vo/voices")
def voices():
    try:
        return {"voices": vo.list_voices(), "models": vo.models()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


# ---------- UI

app.mount("/static", StaticFiles(directory=C.STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(C.STATIC_DIR / "index.html")
