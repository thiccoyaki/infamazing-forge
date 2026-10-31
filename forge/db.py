"""SQLite store: ideas, videos (script + refs + VO), reference library, settings, voice pace."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  angle TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',      -- new | used | skipped
  video_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,                     -- best SEO title = folder + file name
  topic TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT '',
  script TEXT NOT NULL DEFAULT '',
  refs TEXT NOT NULL DEFAULT '[]',         -- [{title, url, kind: youtube|web}]
  facts TEXT NOT NULL DEFAULT '[]',        -- verified facts used in the script
  notes TEXT NOT NULL DEFAULT '',
  style_refs TEXT NOT NULL DEFAULT '[]',   -- reference-library ids used for style
  voice_id TEXT NOT NULL DEFAULT '',
  voice_name TEXT NOT NULL DEFAULT '',
  vo_seconds REAL,
  vo_raw_seconds REAL,
  vo_hash TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',    -- draft | vo | in progress | live
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  packaged_at TEXT,
  folder TEXT NOT NULL DEFAULT ''          -- current folder name under Videos/ (renames follow the title)
);
CREATE TABLE IF NOT EXISTS refs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  format TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',         -- past | youtube | paste | csv
  url TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  views INTEGER NOT NULL DEFAULT 0,
  video_id INTEGER,
  created_at TEXT NOT NULL,
  used_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voice_pace (
  voice_id TEXT PRIMARY KEY, voice_name TEXT NOT NULL DEFAULT '',
  samples INTEGER NOT NULL DEFAULT 0, chars_per_sec REAL NOT NULL
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn():
    c = sqlite3.connect(C.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        c.execute("UPDATE videos SET status='live' WHERE status='done'")  # old "Mark done" = published


JSON_COLS = {"refs", "facts", "style_refs"}


def _d(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        try:
            d[k] = json.loads(d[k] or "[]")
        except json.JSONDecodeError:
            d[k] = []
    return d


# ---------- settings

def get_setting(key: str, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_setting(key: str, value) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))


# ---------- ideas

def list_ideas() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM ideas ORDER BY status='new' DESC, id DESC")]


def add_idea(title: str, angle="", format="", source="") -> int:
    with conn() as c:
        return c.execute("INSERT INTO ideas(title,angle,format,source,created_at) VALUES(?,?,?,?,?)",
                         (title, angle, format, source, now())).lastrowid


def update_idea(iid: int, **f) -> None:
    f = {k: v for k, v in f.items() if k in {"title", "angle", "format", "status", "video_id"}}
    if f:
        with conn() as c:
            c.execute(f"UPDATE ideas SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), iid))


def get_idea(iid: int) -> dict | None:
    with conn() as c:
        return _d(c.execute("SELECT * FROM ideas WHERE id=?", (iid,)).fetchone())


def delete_idea(iid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM ideas WHERE id=?", (iid,))


# ---------- videos

def list_videos() -> list[dict]:
    with conn() as c:
        return [_d(r) for r in c.execute("SELECT * FROM videos ORDER BY id DESC")]


def get_video(vid: int) -> dict | None:
    with conn() as c:
        return _d(c.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone())


def create_video(**f) -> int:
    t = now()
    f = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in f.items()}
    f.setdefault("created_at", t)
    f.setdefault("updated_at", t)
    with conn() as c:
        return c.execute(f"INSERT INTO videos({', '.join(f)}) VALUES({', '.join('?' * len(f))})",
                         tuple(f.values())).lastrowid


def update_video(vid: int, touch: bool = True, **f) -> None:
    f = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in f.items()}
    if touch:
        f["updated_at"] = now()
    with conn() as c:
        c.execute(f"UPDATE videos SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), vid))


def mark_packaged(vid: int) -> None:
    with conn() as c:
        c.execute("UPDATE videos SET packaged_at=? WHERE id=?", (now(), vid))


def delete_video(vid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM videos WHERE id=?", (vid,))
        c.execute("UPDATE ideas SET status='new', video_id=NULL WHERE video_id=?", (vid,))


# ---------- reference library

def list_refs() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM refs ORDER BY COALESCE(used_at, created_at) DESC")]


def get_ref(rid: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM refs WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else None


def add_ref(title: str, body: str, **f) -> int:
    with conn() as c:
        if f.get("video_id"):  # a finished video re-added just updates its reference
            r = c.execute("SELECT id FROM refs WHERE video_id=?", (f["video_id"],)).fetchone()
            if r:
                c.execute("UPDATE refs SET title=?, body=?, format=? WHERE id=?", (title, body, f.get("format", ""), r[0]))
                return r[0]
        cols = {"title": title, "body": body, "created_at": now(), **f}
        return c.execute(f"INSERT INTO refs({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                         tuple(cols.values())).lastrowid


def touch_refs(ids: list[int]) -> None:
    with conn() as c:
        c.executemany("UPDATE refs SET used_at=? WHERE id=?", [(now(), i) for i in ids])


def delete_ref(rid: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM refs WHERE id=?", (rid,))


def all_titles() -> list[str]:
    """Every title the channel already has (videos, library, used ideas) so ideas don't repeat."""
    with conn() as c:
        rows = c.execute("SELECT title FROM videos UNION SELECT title FROM refs "
                         "UNION SELECT title FROM ideas WHERE status!='skipped'").fetchall()
    return [r[0] for r in rows if r[0]]


# ---------- voice pace (chars/sec after cleanup, auto-calibrated)

def get_pace(voice_id: str) -> float:
    with conn() as c:
        r = c.execute("SELECT chars_per_sec FROM voice_pace WHERE voice_id=?", (voice_id,)).fetchone()
    return r[0] if r else C.DEFAULT_CHARS_PER_SEC


def record_pace(voice_id: str, voice_name: str, chars: int, seconds: float) -> float:
    if seconds <= 0 or not voice_id:
        return get_pace(voice_id)
    cps = chars / seconds
    with conn() as c:
        r = c.execute("SELECT samples, chars_per_sec FROM voice_pace WHERE voice_id=?", (voice_id,)).fetchone()
        if r:
            n = min(r[0], 9)
            new = (r[1] * n + cps) / (n + 1)
            c.execute("UPDATE voice_pace SET samples=?, chars_per_sec=?, voice_name=? WHERE voice_id=?",
                      (r[0] + 1, new, voice_name, voice_id))
            return new
        c.execute("INSERT INTO voice_pace VALUES(?,?,1,?)", (voice_id, voice_name, cps))
    return cps
