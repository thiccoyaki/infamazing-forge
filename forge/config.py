"""Paths, env, and tunables for infamazing-forge."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "forge.db"
FORMATS_DIR = ROOT / "formats"
VIDEOS_DIR = ROOT / "Videos"
STATIC_DIR = ROOT / "app" / "static"
ENV_FILE = ROOT / ".env"

PORT = int(os.environ.get("FORGE_PORT", "8778"))  # story-forge uses 8777, so both can run at once

DEFAULT_CHARS_PER_SEC = 17.0
LENGTH_TOLERANCE_PASSES = 5

DEFAULT_LLM_MODEL = "sonnet"
LLM_MODELS = ["sonnet", "opus"]

# Fallbacks only. On first start the real Kalemod VO settings are imported from story-forge's forge.db.
DEFAULT_DEADAIR = {"min_pause": 0.1, "pause_to": 0.05, "speed": 1.3, "threshold": -35.0, "pitch_shift": True, "hq": False}
DEFAULT_VOICE_SETTINGS = {"model": "eleven_multilingual_v2", "stability": 0.5, "similarity": 0.75, "style": 0.0}


def load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def elevenlabs_key() -> str:
    load_env()
    k = os.environ.get("ELEVENLABS_API_KEY", "")
    if not k:
        raise RuntimeError("No ElevenLabs key. Put ELEVENLABS_API_KEY=... in .env (or it's copied from story-forge's .env).")
    return k


def yt_key() -> str:
    load_env()
    return os.environ.get("YT_API_KEY", "")


def story_forge_dir() -> Path:
    """Where story-forge lives (its forge.db holds the Kalemod VO settings). Default: sibling folder."""
    load_env()
    p = os.environ.get("STORY_FORGE_DIR")
    return Path(p) if p else ROOT.parent / "story-forge"


for _d in (FORMATS_DIR, VIDEOS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
