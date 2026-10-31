"""Mirror Videos/<title>/ folders into a Google Drive folder through Google Drive for desktop
(it syncs a normal local folder, so there's no Google login inside the app).

Target: the local path of the Drive folder, e.g. G:\\My Drive\\Infamazing\\FORGE. Set it in Settings or as
FORGE_DRIVE_DIR in .env. A folder that was shared with you needs "Add shortcut to Drive" first so it shows
up under My Drive."""
import os
import shutil
import threading
from pathlib import Path

from . import config as C, db

_lock = threading.Lock()


_detected: str | None = None


def clean_path(p: str) -> str:
    """Explorer's "Copy as path" wraps it in quotes; people also paste trailing slashes."""
    return (p or "").strip().strip('"').strip("'").strip().rstrip("\\/") or ""


def detect(force: bool = False) -> str:
    """Find the Infamazing folder in Google Drive for desktop on any drive letter (every signed-in account gets
    its own letter): a folder with "infamazing" in its name under My Drive or Shared drives, up to 3 levels deep.
    Prefers ones also named forge / scripts / vo."""
    global _detected
    if _detected is not None and not force:
        return _detected
    found = []
    if os.name == "nt":
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for top in ("My Drive", "Shared drives"):
                root = Path(f"{letter}:\\") / top
                try:
                    if not root.is_dir():
                        continue
                    level = [root]
                    for depth in range(3):
                        nxt = []
                        for d in level:
                            for c in d.iterdir():
                                if c.is_dir():
                                    nxt.append(c)
                                    if "infamazing" in c.name.lower():
                                        found.append((depth, c))
                        level = nxt[:400]
                except OSError:
                    continue
    def rank(x):
        depth, p = x
        n = p.name.lower()
        return (-sum(k in n for k in ("forge", "script", "vo", "video")), -depth)
    _detected = str(sorted(found, key=rank)[0][1]) if found else ""
    return _detected


def target() -> str:
    C.load_env()
    t = clean_path(os.environ.get("FORGE_DRIVE_DIR") or db.get_setting("drive_dir", "") or "")
    if t.lower() == "off":
        return ""
    return t or detect()


def status() -> dict:
    t = target()
    manual = clean_path(os.environ.get("FORGE_DRIVE_DIR") or db.get_setting("drive_dir", "") or "")
    return {"dir": t, "ok": bool(t) and Path(t).is_dir(), "auto": bool(t) and not manual,
            "last": db.get_setting("drive_last", {}), "url": db.get_setting("drive_url", "")}


def mirror(local: Path, old_name: str | None = None, log: list | None = None) -> str | None:
    t = target()
    if not t:
        return None
    root = Path(t)
    if not root.is_dir():
        raise RuntimeError(f"Drive folder not found: {t} (is Google Drive for desktop running and signed in?)")
    dest = root / local.name
    with _lock:
        if old_name and old_name != local.name and (root / old_name).is_dir() and not dest.exists():
            (root / old_name).rename(dest)  # title changed: move the Drive copy instead of leaving a duplicate
        dest.mkdir(parents=True, exist_ok=True)
        names = {p.name for p in local.iterdir() if p.is_file()}
        for n in names:
            src, dst = local / n, dest / n
            if not dst.exists() or src.stat().st_size != dst.stat().st_size or src.stat().st_mtime > dst.stat().st_mtime + 1:
                shutil.copy2(src, dst)
        if old_name and old_name != local.name:  # old-title script/VO left over after a rename; your own files stay
            for ext in (".txt", ".mp3"):
                stale = dest / f"{old_name}{ext}"
                if stale.exists() and f"{local.name}{ext}" in names:
                    stale.unlink()
    db.set_setting("drive_last", {"at": db.now(), "folder": local.name})
    if log is not None:
        log.append(f"synced to Drive: {dest}")
    return str(dest)


def mirror_now(local: Path, old_name: str | None = None) -> str:
    """Mirror and return a one-line status for logs / toasts. Never raises."""
    try:
        dest = mirror(local, old_name)
        return f"Drive: synced to {dest}" if dest else "Drive: no folder set (Settings → Google Drive), saved locally only"
    except Exception as e:  # noqa: BLE001
        db.set_setting("drive_last", {"at": db.now(), "folder": local.name, "error": str(e)})
        return f"Drive: FAILED: {e}"


def mirror_async(local: Path, old_name: str | None = None) -> None:
    def go():
        try:
            mirror(local, old_name)
        except Exception as e:  # noqa: BLE001 - Drive problems never break Save
            db.set_setting("drive_last", {"at": db.now(), "folder": local.name, "error": str(e)})
    threading.Thread(target=go, daemon=True).start()


def mirror_all(log: list) -> dict:
    n = 0
    for d in sorted(p for p in C.VIDEOS_DIR.iterdir() if p.is_dir()):
        mirror(d, log=log)
        n += 1
    return {"synced": n}
