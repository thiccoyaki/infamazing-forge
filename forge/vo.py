"""ElevenLabs VO -> dead-air cleanup, with the same voice + settings Kalemod uses in story-forge."""
import json
import sqlite3
from pathlib import Path

from . import config as C, db
from vendor.narration_cleaner import deadair, elevenlabs_tts as el


# ---------- Kalemod settings (read from story-forge's forge.db, read-only)

def import_kalemod(log: list | None = None) -> dict:
    """Copy story-forge's last-used voice, voice settings, dead-air settings and voice pace into this app,
    and its ElevenLabs / YouTube keys into .env when this app has none."""
    sf = C.story_forge_dir()
    out = {"found": False, "dir": str(sf)}
    dbp = sf / "forge.db"
    if dbp.exists():
        c = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        try:
            rows = dict(c.execute("SELECT key, value FROM settings WHERE key IN "
                                  "('default_voice','voice_settings','deadair')").fetchall())
            for k, v in rows.items():
                db.set_setting(k, json.loads(v))
                out[k] = json.loads(v)
            for vid, name, n, cps in c.execute("SELECT voice_id, voice_name, samples, chars_per_sec FROM voice_pace"):
                with db.conn() as mine:
                    mine.execute("INSERT OR IGNORE INTO voice_pace VALUES(?,?,?,?)", (vid, name, n, cps))
        finally:
            c.close()
        out["found"] = True
        db.set_setting("kalemod_imported", db.now())
    env = sf / ".env"
    if env.exists():
        have = C.ENV_FILE.read_text(encoding="utf-8") if C.ENV_FILE.exists() else ""
        add = []
        for line in env.read_text(encoding="utf-8").splitlines():
            k = line.split("=", 1)[0].strip()
            if k in ("ELEVENLABS_API_KEY", "YT_API_KEY", "SHORTS_YTDLP_COOKIES") and "=" in line \
                    and not line.strip().startswith("#") and f"\n{k}=" not in "\n" + have:
                add.append(line.strip())
        if add:
            C.ENV_FILE.write_text(have.rstrip("\n") + ("\n" if have else "") + "\n".join(add) + "\n", encoding="utf-8")
            out["keys"] = [a.split("=")[0] for a in add]
    if log is not None:
        log.append(f"story-forge: {sf} ({'found' if out['found'] else 'forge.db not found'})")
    return out


def voice_defaults() -> dict:
    return {"voice": db.get_setting("default_voice", {}),
            "settings": {**C.DEFAULT_VOICE_SETTINGS, **(db.get_setting("voice_settings", {}) or {})},
            "deadair": {**C.DEFAULT_DEADAIR, **(db.get_setting("deadair", {}) or {})}}


# ---------- ElevenLabs

def list_voices() -> list[dict]:
    return [{"voice_id": v.voice_id, "name": v.name, "display": v.display} for v in el.list_voices(C.elevenlabs_key())]


def models() -> list[dict]:
    return [{"id": m, "label": lbl} for m, lbl in el.MODELS]


def have_ffmpeg() -> bool:
    try:
        return not deadair.have_ffmpeg()
    except Exception:  # noqa: BLE001
        return False


def duration_of(path: Path) -> float:
    return deadair.probe(str(path)).duration


def make(text: str, raw: Path, clean: Path, voice_id: str, settings: dict, dead: dict, log: list) -> tuple[float, float]:
    """raw mp3 from ElevenLabs, then dead-air cleanup + speed into clean. Returns (raw_s, clean_s)."""
    opts = el.TTSOptions(api_key=C.elevenlabs_key(), voice_id=voice_id, model=settings.get("model") or el.DEFAULT_MODEL,
                         stability=float(settings.get("stability", 0.5)),
                         similarity_boost=float(settings.get("similarity", 0.75)), style=float(settings.get("style", 0)))
    raw.parent.mkdir(parents=True, exist_ok=True)
    log.append("ElevenLabs: generating…")
    el.generate(text, opts, str(raw))
    raw_s = duration_of(raw)
    log.append(f"raw: {raw_s:.1f}s")
    if not have_ffmpeg():
        log.append("ffmpeg not found: skipped dead-air cleanup, clean = raw")
        clean.write_bytes(raw.read_bytes())
        return raw_s, raw_s
    d = deadair.Options(min_pause=float(dead["min_pause"]), pause_to=float(dead["pause_to"]),
                        threshold=float(dead.get("threshold", -35.0)), speed=float(dead.get("speed", 1.0)),
                        pitch_shift=bool(dead.get("pitch_shift", False)), hq=bool(dead.get("hq", False)), overwrite=True)
    # ffmpeg works on short temp paths, then Python moves the result into place: long Windows paths
    # (repo path + title folder + title file name) can pass 260 chars, which ffmpeg can't open
    import shutil
    import tempfile
    import uuid
    tmp = Path(tempfile.gettempdir()) / f"ifr_{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        shutil.copyfile(raw, tmp / "raw.mp3")
        r = deadair.process_file(str(tmp / "raw.mp3"), str(tmp / "clean.mp3"), d)
        if r.error or not (tmp / "clean.mp3").exists():
            raise RuntimeError("dead-air cleanup failed: " + (r.error or "no output file")[-1500:])
        shutil.move(str(tmp / "clean.mp3"), str(clean))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    clean_s = duration_of(clean)
    log.append(f"clean: {clean_s:.1f}s -> {clean.name}")
    return raw_s, clean_s
