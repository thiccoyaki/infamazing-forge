"""Reference library from video links: yt-dlp captions (ElevenLabs speech-to-text if there are none),
then Claude tidies the transcript into one clean paragraph. (fetch() is shared with story-forge.)"""
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import config as C, db, llm


def _vtt_to_text(vtt: Path) -> str:
    lines, last = [], ""
    for raw in vtt.read_text(encoding="utf-8", errors="ignore").splitlines():
        t = raw.strip()
        if not t or "-->" in t or t.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or t.isdigit():
            continue
        t = re.sub(r"<[^>]+>", "", t)
        if t and t != last:
            lines.append(t)
            last = t
    return " ".join(lines)


def _ydl_base(out: Path) -> dict:
    C.load_env()
    opts = {"outtmpl": str(out / "%(id)s.%(ext)s"), "quiet": True, "no_warnings": True, "noprogress": True,
            "retries": 3, "extractor_retries": 3,
            # YouTube needs a JS runtime to unlock formats; yt-dlp only tries Deno by default, Node is already installed
            "js_runtimes": {"deno": {}, "node": {}}}
    browser = os.environ.get("SHORTS_YTDLP_COOKIES")  # same optional setting shorts-engine uses
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _stt(audio: Path) -> str:
    """ElevenLabs speech-to-text (uses your ElevenLabs key)."""
    key = C.elevenlabs_key()
    boundary = "----forge" + os.urandom(8).hex()
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n'.encode(),
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
             f'Content-Type: application/octet-stream\r\n\r\n'.encode(), audio.read_bytes(), f"\r\n--{boundary}--\r\n".encode()]
    req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=b"".join(parts), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read()).get("text", "")


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _caption_text(info: dict, log: list) -> str:
    """Fetch the caption track straight from its URL (with backoff), instead of yt-dlp's subtitle writer,
    which fails the whole video on a single 429."""
    import time
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key) or {}
        lang = next((l for l in ("en", "en-orig", "en-US", "en-GB") if l in tracks), None)
        if not lang:
            continue
        fmt = next((f for f in tracks[lang] if f.get("ext") == "vtt"), None)
        if not fmt:
            continue
        for wait in (0, 5):  # YouTube throttles caption requests hard; don't wait long, audio + STT is the fallback
            if wait:
                log.append(f"  captions rate-limited, retrying in {wait}s…")
                time.sleep(wait)
            try:
                req = urllib.request.Request(fmt["url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    with tempfile.NamedTemporaryFile("wb", suffix=".vtt", delete=False) as f:
                        f.write(r.read())
                text = _vtt_to_text(Path(f.name))
                os.unlink(f.name)
                return text
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    break
    return ""


def fetch(url: str, log: list) -> dict:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("yt-dlp isn't installed: restart start.bat (it installs requirements)") from e
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        info, errs = None, []
        base = _ydl_base(out)
        nocookie = {k: v for k, v in base.items() if k != "cookiesfrombrowser"}
        for opts in (base, nocookie, {**nocookie, "extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb"]}}}):
            try:  # browser cookies often can't be read (browser open / not installed), so also try without
                with yt_dlp.YoutubeDL({**opts, "skip_download": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                break
            except Exception as e:  # noqa: BLE001
                errs.append(ANSI.sub("", str(e)).replace("ERROR: ", "").splitlines()[0][:160])
        if info is None:
            raise RuntimeError("couldn't read the video: " + " | ".join(dict.fromkeys(errs)))
        text, source = _caption_text(info, log), "captions"
        if not text:
            log.append("  no captions available: transcribing the audio with ElevenLabs…")
            audio = None
            base = {**_ydl_base(out), "format": "ba/b"}
            errs = []
            # same fallbacks shorts-engine uses when YouTube hides formats or rate-limits a client
            for opts in (base, {k: v for k, v in base.items() if k != "cookiesfrombrowser"},
                         {**base, "extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb"]}}},
                         {**{k: v for k, v in base.items() if k != "cookiesfrombrowser"},
                          "extractor_args": {"youtube": {"player_client": ["android_vr", "tv", "ios"]}}}):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.extract_info(url, download=True)
                    audio = next((p for p in out.glob(f"{info['id']}.*") if p.suffix not in (".vtt", ".json", ".part")), None)
                    if audio:
                        break
                except Exception as e:  # noqa: BLE001
                    errs.append(ANSI.sub("", str(e)).replace("ERROR: ", "").splitlines()[0][:160])
                    continue
            if not audio:
                raise RuntimeError("couldn't get captions or audio: " + " | ".join(dict.fromkeys(errs)))
            text, source = _stt(audio), "speech-to-text"
    if not text.strip():
        raise RuntimeError("no speech found in this video")
    return {"id": info["id"], "url": info.get("webpage_url") or url, "title": info.get("title", ""),
            "channel": info.get("channel") or info.get("uploader", ""), "views": info.get("view_count") or 0,
            "likes": info.get("like_count") or 0, "duration": info.get("duration") or 0, "transcript": text, "source": source}


def tidy(v: dict, llm_model: str) -> dict:
    prompt = (
        "This is an auto-generated transcript of a YouTube Short (trivia / story narration). Return JSON only:\n"
        '{"script": "...", "title": "short working title"}\n'
        "script: the narration as ONE clean paragraph. Fix punctuation, capitalization and obvious mis-heard words; "
        "drop [music] tags and a channel intro/outro. Do NOT rewrite, summarize or add anything.\n\n"
        f"VIDEO TITLE: {v['title']}\nTRANSCRIPT:\n{v['transcript']}"
    )
    data = llm.ask_json(prompt, model=llm_model)
    return data if isinstance(data, dict) else {}


def add_from_links(urls: list[str], fmt: str, tidy_on: bool, llm_model: str, log: list) -> dict:
    import time
    added = []
    have = {r["url"] for r in db.list_refs() if r["url"]}
    for url in urls:
        try:
            log.append(f"{url}: fetching…")
            v = fetch(url, log)
            if v["url"] in have:
                log.append(f"  already in the library: {v['title']}")
                continue
            t = tidy(v, llm_model) if tidy_on else {}
            body = (t.get("script") or v["transcript"]).strip()
            rid = db.add_ref(v["title"] or t.get("title", ""), body, format=fmt, source="youtube", url=v["url"],
                             channel=v["channel"], views=int(v["views"] or 0))
            have.add(v["url"])
            added.append(rid)
            log.append(f"  added: {v['title']} ({len(body)} chars, {v['source']})")
        except Exception as e:  # noqa: BLE001 - one bad link shouldn't stop the batch
            log.append(f"  failed: {ANSI.sub('', str(e)).replace('ERROR: ', '').splitlines()[0][:200]}")
        time.sleep(3)  # space links out so YouTube doesn't rate-limit the batch
    return {"added": len(added), "ids": added}
