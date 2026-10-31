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


def target() -> str:
    C.load_env()
    t = (os.environ.get("FORGE_DRIVE_DIR") or db.get_setting("drive_dir", "") or "").strip()
    return "" if t.lower() == "off" else t


def status() -> dict:
    t = target()
    return {"dir": t, "ok": bool(t) and Path(t).is_dir(), "last": db.get_setting("drive_last", {}),
            "url": db.get_setting("drive_url", "")}


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
