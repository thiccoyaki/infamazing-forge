#!/usr/bin/env python3
"""
elevenlabs_tts.py — ElevenLabs text-to-speech wrapper for Narration Cleaner.

Bulk-converts scripts to spoken audio via the ElevenLabs API.
No dependencies beyond the standard library (uses urllib).

Importable as a library (the GUI uses it) or run standalone:

    python elevenlabs_tts.py --key sk_... --voice Adam --out-dir voices/ scripts/*.txt
    python elevenlabs_tts.py --key sk_... --list-voices
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "TTSError", "Voice", "TTSOptions", "TTSResult",
    "list_voices", "generate", "generate_bulk",
]

API_BASE = "https://api.elevenlabs.io/v1"

MODELS = [
    ("eleven_v4",              "v4 (newest, 90+ languages)"),
    ("eleven_v3",              "v3 (70+ languages)"),
    ("eleven_flash_v2_5",      "Flash v2.5 (fastest, 32 languages)"),
    ("eleven_multilingual_v2", "Multilingual v2 (29 languages)"),
    ("eleven_monolingual_v1",  "English v1 (legacy)"),
]

DEFAULT_MODEL = "eleven_v4"

OUTPUT_FORMATS = [
    ("mp3_44100_192", "MP3 192 kbps"),
    ("mp3_44100_128", "MP3 128 kbps"),
    ("mp3_44100_64",  "MP3 64 kbps"),
    ("pcm_44100",     "PCM 44.1 kHz (WAV)"),
    ("pcm_24000",     "PCM 24 kHz (WAV)"),
]

DEFAULT_FORMAT = "mp3_44100_192"


class TTSError(Exception):
    """Something went wrong with a TTS call."""


# ---------------------------------------------------------------- data types


@dataclass
class Voice:
    voice_id: str
    name: str
    category: str         # "premade", "cloned", "generated", "professional"
    description: str = ""
    labels: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        tag = ""
        if self.category == "cloned":
            tag = " [cloned]"
        elif self.category == "generated":
            tag = " [generated]"
        elif self.category == "professional":
            tag = " [professional]"
        return f"{self.name}{tag}"


@dataclass
class TTSOptions:
    api_key: str
    voice_id: str
    model: str = DEFAULT_MODEL
    output_format: str = DEFAULT_FORMAT
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0


@dataclass
class TTSResult:
    input_path: str          # source text file, or "<inline>" for direct text
    output_path: str         # written audio file
    characters: int          # characters sent
    error: str = ""          # non-empty means this one failed


# ---------------------------------------------------------------- API helpers


def _api(path: str, api_key: str, method: str = "GET",
         body: bytes | None = None,
         content_type: str = "application/json",
         timeout: int = 120) -> bytes:
    """Raw HTTPS call to the ElevenLabs API. Returns the response body."""
    url = f"{API_BASE}/{path.lstrip('/')}"
    req = urllib.request.Request(url, method=method)
    req.add_header("xi-api-key", api_key)
    if body is not None:
        req.add_header("Content-Type", content_type)
        req.data = body
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(msg).get("detail", {})
            if isinstance(detail, dict):
                msg = detail.get("message", msg)
            elif isinstance(detail, str):
                msg = detail
        except (json.JSONDecodeError, AttributeError):
            pass
        raise TTSError(f"ElevenLabs API {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise TTSError(f"Could not reach ElevenLabs: {exc.reason}") from exc


def _json_api(path: str, api_key: str) -> Any:
    return json.loads(_api(path, api_key))


# ---------------------------------------------------------------- voices


def list_voices(api_key: str) -> list[Voice]:
    """Fetch all voices the key can see: premade defaults + user library."""
    data = _json_api("voices", api_key)
    voices: list[Voice] = []
    for v in data.get("voices", []):
        voices.append(Voice(
            voice_id=v["voice_id"],
            name=v.get("name", "Unknown"),
            category=v.get("category", "premade"),
            description=v.get("description") or "",
            labels=v.get("labels") or {},
        ))
    # Sort: premade first (alphabetically), then the rest alphabetically
    order = {"premade": 0, "professional": 1, "cloned": 2, "generated": 3}
    voices.sort(key=lambda v: (order.get(v.category, 9), v.name.lower()))
    return voices


def get_subscription(api_key: str) -> dict:
    """Return the user's subscription info (character quota, etc.)."""
    return _json_api("user/subscription", api_key)


# ---------------------------------------------------------------- generate


def _ext_for_format(fmt: str) -> str:
    """File extension (with dot) for an ElevenLabs output format."""
    if fmt.startswith("mp3"):
        return ".mp3"
    if fmt.startswith("pcm"):
        return ".wav"
    if fmt.startswith("ulaw"):
        return ".wav"
    return ".mp3"


def _wrap_pcm_as_wav(pcm: bytes, sample_rate: int = 44100) -> bytes:
    """Wrap raw PCM s16le bytes in a WAV header."""
    import struct
    channels = 1
    bits = 16
    block_align = channels * (bits // 8)
    byte_rate = sample_rate * block_align
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels,
        sample_rate, byte_rate, block_align, bits,
        b"data", data_size,
    )
    return header + pcm


def generate(text: str, opts: TTSOptions, output_path: str) -> int:
    """Generate speech for a single text. Returns character count.

    Writes the audio to output_path. Raises TTSError on failure.
    """
    payload = json.dumps({
        "text": text,
        "model_id": opts.model,
        "voice_settings": {
            "stability": opts.stability,
            "similarity_boost": opts.similarity_boost,
            "style": opts.style,
        },
    }).encode("utf-8")

    path = f"text-to-speech/{opts.voice_id}?output_format={opts.output_format}"
    audio = _api(path, opts.api_key, method="POST", body=payload, timeout=300)

    # PCM formats return raw samples — wrap in a WAV container
    if opts.output_format.startswith("pcm"):
        rate_str = opts.output_format.split("_")[1] if "_" in opts.output_format else "44100"
        audio = _wrap_pcm_as_wav(audio, int(rate_str))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio)

    return len(text)


def generate_bulk(
    items: list[tuple[str, str]],   # (text_or_path, output_path)
    opts: TTSOptions,
    *,
    read_files: bool = True,
    cancel: threading.Event | None = None,
    on_progress: Callable[[int, int, TTSResult], None] | None = None,
) -> list[TTSResult]:
    """Generate speech for a list of items.

    Each item is (source, output_path) where source is a file path (read as
    text when read_files=True) or raw text (when read_files=False).

    on_progress(done_count, total_count, latest_result) is called after each.
    """
    results: list[TTSResult] = []
    total = len(items)

    for i, (source, out_path) in enumerate(items):
        if cancel and cancel.is_set():
            results.append(TTSResult(
                input_path=source, output_path=out_path,
                characters=0, error="Cancelled",
            ))
            continue

        try:
            if read_files and os.path.isfile(source):
                with open(source, encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                input_label = source
            else:
                text = source.strip()
                input_label = "<inline>"

            if not text:
                raise TTSError("Empty text — nothing to generate")

            chars = generate(text, opts, out_path)
            r = TTSResult(input_path=input_label, output_path=out_path,
                          characters=chars)
        except (TTSError, OSError) as exc:
            r = TTSResult(input_path=source, output_path=out_path,
                          characters=0, error=str(exc))

        results.append(r)
        if on_progress:
            on_progress(i + 1, total, r)

    return results


def _sanitize_filename(name: str) -> str:
    """Turn a script label into a safe filename stem."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip(". ")
    return name[:120] or "untitled"


# ---------------------------------------------------------------- CLI


def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="elevenlabs_tts",
        description="Bulk text-to-speech via the ElevenLabs API.",
    )
    p.add_argument("inputs", nargs="*",
                   help="Text files to convert (or use --text for inline)")
    p.add_argument("--key", required=True,
                   help="ElevenLabs API key")
    p.add_argument("--voice", default="Adam",
                   help="Voice name or ID (default: Adam)")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Model ID (default: {DEFAULT_MODEL})")
    p.add_argument("--format", default=DEFAULT_FORMAT, dest="out_fmt",
                   help=f"Output format (default: {DEFAULT_FORMAT})")
    p.add_argument("--text", action="append", default=[],
                   help="Inline text to generate (repeatable)")
    p.add_argument("-d", "--out-dir", default=".",
                   help="Output directory (default: current)")
    p.add_argument("--list-voices", action="store_true",
                   help="List available voices and exit")
    p.add_argument("--stability", type=float, default=0.5)
    p.add_argument("--similarity", type=float, default=0.75)
    p.add_argument("--style", type=float, default=0.0)
    args = p.parse_args()

    # List voices
    if args.list_voices:
        print("Fetching voices…")
        voices = list_voices(args.key)
        cur_cat = None
        for v in voices:
            if v.category != cur_cat:
                cur_cat = v.category
                print(f"\n── {cur_cat.title()} ──")
            desc = f"  ({v.description})" if v.description else ""
            labels = ", ".join(f"{k}: {val}" for k, val in v.labels.items())
            if labels:
                desc += f"  [{labels}]"
            print(f"  {v.name:24s}  {v.voice_id}{desc}")
        return

    # Resolve voice name -> ID
    voice_id = args.voice
    if not voice_id.startswith("21") and len(voice_id) < 20:
        # Looks like a name, not an ID — look it up
        voices = list_voices(args.key)
        match = [v for v in voices if v.name.lower() == voice_id.lower()]
        if not match:
            print(f"Voice '{voice_id}' not found. Available voices:")
            for v in voices:
                print(f"  {v.name}")
            sys.exit(1)
        voice_id = match[0].voice_id

    opts = TTSOptions(
        api_key=args.key,
        voice_id=voice_id,
        model=args.model,
        output_format=args.out_fmt,
        stability=args.stability,
        similarity_boost=args.similarity,
        style=args.style,
    )

    ext = _ext_for_format(args.out_fmt)

    # Build items list
    items: list[tuple[str, str]] = []
    for path in args.inputs:
        stem = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(args.out_dir, stem + ext)
        items.append((path, out))
    for i, text in enumerate(args.text):
        name = _sanitize_filename(text[:40])
        out = os.path.join(args.out_dir, f"{name}{ext}")
        items.append((text, out))

    if not items:
        p.error("No inputs. Pass text files or --text '…'")

    os.makedirs(args.out_dir, exist_ok=True)

    def on_progress(done: int, total: int, r: TTSResult) -> None:
        status = "OK" if not r.error else f"FAILED: {r.error}"
        print(f"[{done}/{total}] {os.path.basename(r.output_path)}: "
              f"{r.characters} chars — {status}")

    results = generate_bulk(
        items, opts,
        read_files=True,
        on_progress=on_progress,
    )

    # Summary
    ok = [r for r in results if not r.error]
    bad = [r for r in results if r.error]
    total_chars = sum(r.characters for r in ok)
    print(f"\nDone: {len(ok)}/{len(results)} files, "
          f"{total_chars:,} characters used.")
    if bad:
        print(f"\nFailed ({len(bad)}):")
        for r in bad:
            print(f"  {r.input_path}: {r.error}")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
