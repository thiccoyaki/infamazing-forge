#!/usr/bin/env python3
"""
deadair.py — detect dead air / pauses in audio, cut or shorten them, and
retime the result to any speed (pitch preserved or pitch shifted).

Everything heavy runs inside ffmpeg:
  pass 1  ffmpeg silencedetect  -> pause list (streaming, no full decode into Python)
  pass 2  ffmpeg aselect+asetpts -> drops the dead-air frames, then atempo /
          rubberband (pitch preserved) or asetrate (pitch shifted) for speed

Requires ffmpeg + ffprobe: either on PATH, or sitting next to this file (or
next to the .exe, or in a vendor/ or bin/ subfolder). No Python dependencies.
Usable as a CLI or imported as a library (see deadair_gui.py).

Examples
  # report only — what would be cut, nothing written
  deadair.py talk.mp3 --dry-run

  # kill any pause over 0.8s down to 0.25s, keep speed
  deadair.py talk.mp3 -o tight.mp3 --min-pause 0.8 --pause-to 0.25

  # same, then play it back 1.25x with the voice pitch intact
  deadair.py talk.mp3 -o fast.mp3 --min-pause 0.8 --pause-to 0.2 --speed 1.25

  # chipmunk / tape mode: speed AND pitch move together
  deadair.py talk.wav -o tape.wav --speed 1.5 --pitch-shift

  # slow it down, pitch intact, highest quality stretch
  deadair.py talk.wav -o slow.wav --speed 0.85 --hq

  # only retime, leave pauses alone
  deadair.py talk.mp3 -o slow.mp3 --speed 0.9 --no-cut

  # bulk: every mp3 in a folder, results beside the sources as *_tight.mp3
  deadair.py episodes/ --speed 1.2

  # bulk into one folder, forced to mp3, four at a time
  deadair.py raw/*.wav -d done/ --format mp3 --jobs 4

  # bulk, whole tree, skipping anything already done
  deadair.py library/ -r -d done/ --skip-existing
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field

__all__ = [
    "DeadAirError", "Stream", "probe", "detect_pauses", "plan_keeps",
    "speed_filters", "build_filtergraph", "render", "has_rubberband",
    "have_ffmpeg", "tool", "hms", "AUDIO_EXTS",
    "Options", "Result", "analyze", "process_file", "collect_inputs",
    "output_path",
]

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
              ".aiff", ".aif", ".wma", ".mp4", ".mov", ".mkv", ".webm")


class DeadAirError(Exception):
    """Anything the user needs to fix. The CLI prints it; the GUI shows it."""


# ---------------------------------------------------------------- utilities


def die(msg: str):
    raise DeadAirError(msg)


_TOOLS: dict[str, str] = {}


def tool(name: str) -> str:
    """Absolute path to ffmpeg/ffprobe, preferring a copy shipped with the app.

    Search order: alongside the frozen .exe (or this script), then a vendor/
    or bin/ subfolder there, then inside the PyInstaller one-file bundle,
    then PATH. Falls back to the bare name so the error message stays sane.
    """
    if name in _TOOLS:
        return _TOOLS[name]

    exe = name + (".exe" if sys.platform == "win32" else "")
    roots: list[str] = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            roots.append(bundle)
    else:
        roots.append(os.path.dirname(os.path.abspath(__file__)))

    found = None
    for root in roots:
        for cand in (os.path.join(root, exe),
                     os.path.join(root, "vendor", exe),
                     os.path.join(root, "bin", exe),
                     os.path.join(root, "ffmpeg", exe)):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                found = cand
                break
        if found:
            break

    _TOOLS[name] = found or shutil.which(name) or name
    return _TOOLS[name]


def have_ffmpeg() -> list[str]:
    """Return the names of any required binaries that are missing."""
    missing = []
    for name in ("ffmpeg", "ffprobe"):
        resolved = tool(name)
        if not os.path.isabs(resolved) and shutil.which(resolved) is None:
            missing.append(name)
    return missing


# Keep a console window from flashing up for every ffmpeg call when the GUI
# is launched with pythonw.exe on Windows.
_QUIET_SPAWN: dict = (
    {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if sys.platform == "win32" else {}
)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, **_QUIET_SPAWN)


def hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    return f"{int(m)}:{s:05.2f}"


def _stream_ffmpeg(cmd: list[str], total: float, on_progress=None,
                   cancel=None) -> tuple[int, str]:
    """Run ffmpeg, reporting 0..1 completion from its -progress stream.

    stderr is drained on a side thread so a long log can never deadlock the
    pipe. `cancel` is an optional threading.Event; setting it kills ffmpeg.
    """
    if on_progress is not None:
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, **_QUIET_SPAWN)
    err: list[str] = []

    def drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            err.append(line)

    t = threading.Thread(target=drain, daemon=True)
    t.start()

    watcher = None
    if cancel is not None:
        def watch():
            while proc.poll() is None:
                if cancel.wait(0.2):
                    proc.kill()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        if on_progress and total > 0 and line.startswith("out_time_us="):
            raw = line.split("=", 1)[1].strip()
            if raw.isdigit():
                on_progress(min(1.0, int(raw) / 1e6 / total))
    proc.wait()
    t.join(timeout=2)
    if watcher:
        watcher.join(timeout=1)
    return proc.returncode, "".join(err)


# ---------------------------------------------------------------- probing


@dataclass
class Stream:
    duration: float
    sample_rate: int
    channels: int
    codec: str


def probe(path: str) -> Stream:
    p = run([
        tool("ffprobe"), "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,codec_name:format=duration",
        "-of", "json", path,
    ])
    if p.returncode != 0:
        die(f"ffprobe could not read {os.path.basename(path)}\n{p.stderr.strip()}")
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        die(f"no audio stream found in {os.path.basename(path)}")
    st = streams[0]
    dur = data.get("format", {}).get("duration")
    if dur in (None, "N/A"):
        p2 = run([tool("ffprobe"), "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=duration", "-of", "csv=p=0", path])
        dur = p2.stdout.strip() or "0"
    try:
        duration = float(dur)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        die(f"could not determine the duration of {os.path.basename(path)}")
    return Stream(
        duration=duration,
        sample_rate=int(st.get("sample_rate") or 48000),
        channels=int(st.get("channels") or 2),
        codec=str(st.get("codec_name") or "?"),
    )


# ---------------------------------------------------------------- detection

SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_pauses(path: str, threshold_db: float, min_pause: float,
                  duration: float, on_progress=None,
                  cancel=None) -> list[tuple[float, float]]:
    """Return [(start, end)] for every silent stretch >= min_pause."""
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-nostdin",
        "-i", path,
        "-map", "0:a:0",
        "-af", (f"aformat=channel_layouts=mono,"
                f"silencedetect=noise={threshold_db}dB:d={min_pause:.6f}"),
        "-f", "null", "-",
    ]
    code, err = _stream_ffmpeg(cmd, duration, on_progress, cancel)
    if cancel is not None and cancel.is_set():
        raise DeadAirError("cancelled")
    if code != 0:
        die(f"silence detection failed\n{err.strip()[-2000:]}")

    pauses: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in err.splitlines():
        m = SIL_START.search(line)
        if m:
            open_start = max(0.0, float(m.group(1)))
            continue
        m = SIL_END.search(line)
        if m and open_start is not None:
            pauses.append((open_start, min(duration, float(m.group(1)))))
            open_start = None
    if open_start is not None:          # file ends inside a silence
        pauses.append((open_start, duration))
    return [(a, b) for a, b in pauses if b > a]


# ---------------------------------------------------------------- planning


def plan_keeps(pauses: list[tuple[float, float]], pause_to: float,
               duration: float) -> tuple[list[tuple[float, float]], float]:
    """Turn pauses into the list of intervals to KEEP.

    Each pause is shortened to `pause_to` seconds by removing the middle,
    which leaves half the retained silence on each side so speech onsets and
    tails stay natural. Every cut lands inside near-silent audio, so no
    fades or crossfades are needed.
    """
    drops: list[tuple[float, float]] = []
    for s, e in pauses:
        if (e - s) <= pause_to:
            continue
        half = pause_to / 2.0
        drops.append((s + half, e - half))

    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in drops:
        if s > cursor:
            keeps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keeps.append((cursor, duration))

    return keeps, sum(e - s for s, e in drops)


# ---------------------------------------------------------------- filters


def atempo_chain(factor: float) -> list[str]:
    """Split an arbitrary factor into atempo stages inside the safe 0.5-2.0 band."""
    stages: list[float] = []
    f = factor
    while f > 2.0:
        stages.append(2.0)
        f /= 2.0
    while f < 0.5:
        stages.append(0.5)
        f /= 0.5
    stages.append(f)
    return [f"atempo={s:.8f}" for s in stages if abs(s - 1.0) > 1e-9]


def speed_filters(speed: float, sample_rate: int, pitch_shift: bool,
                  hq: bool = False) -> list[str]:
    if abs(speed - 1.0) < 1e-9:
        return []
    if pitch_shift:
        # varispeed / tape: resample the timeline, then land back on the
        # original rate. Duration and pitch move together.
        return [f"asetrate={int(round(sample_rate * speed))}",
                f"aresample={sample_rate}",
                "aformat=sample_fmts=fltp"]
    if hq:
        return [f"rubberband=tempo={speed:.8f}:pitchq=quality"]
    return atempo_chain(speed)


def has_rubberband() -> bool:
    if have_ffmpeg():
        return False
    return "rubberband" in run([tool("ffmpeg"), "-hide_banner", "-filters"]).stdout


# Some ffmpeg builds' expression parser runs out of recursion/memory once a
# single aselect expression chains ~60-80 between() clauses. Past this many
# keeps, the cut is split into chunks, each with its own aselect, and the
# pieces are concatenated back together.
KEEPS_PER_SELECT = 40


def _select_expr(keeps: list[tuple[float, float]]) -> str:
    # Millisecond precision: aselect works on whole audio frames (~20 ms),
    # so more decimals only make the graph bigger, never more accurate.
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keeps)


def build_filtergraph(keeps: list[tuple[float, float]], speed_chain: list[str],
                      cut: bool) -> str:
    if cut and len(keeps) > KEEPS_PER_SELECT:
        groups = [keeps[i:i + KEEPS_PER_SELECT]
                  for i in range(0, len(keeps), KEEPS_PER_SELECT)]
        n = len(groups)
        parts = ["[0:a:0]asplit=" + str(n) + "".join(f"[s{i}]" for i in range(n))]
        for i, g in enumerate(groups):
            parts.append(f"[s{i}]aselect='{_select_expr(g)}',"
                         f"asetpts=N/SR/TB[c{i}]")
        tail = [f"concat=n={n}:v=0:a=1", *speed_chain]
        parts.append("".join(f"[c{i}]" for i in range(n)) + ",".join(tail) + "[out]")
        return ";".join(parts)

    chain: list[str] = []
    if cut and keeps:
        chain.append(f"aselect='{_select_expr(keeps)}'")
        chain.append("asetpts=N/SR/TB")
    chain.extend(speed_chain)
    if not chain:
        chain.append("anull")
    return "[0:a:0]" + ",".join(chain) + "[out]"


# ---------------------------------------------------------------- rendering


def encode_args(out_path: str, bitrate: str | None) -> list[str]:
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".wav", ".flac", ".aiff", ".aif"):
        return []                                   # lossless, let ffmpeg pick
    args: list[str] = []
    if ext == ".mp3":
        args += ["-c:a", "libmp3lame"]
    elif ext in (".m4a", ".mp4", ".aac"):
        args += ["-c:a", "aac"]
    elif ext in (".opus", ".ogg"):
        args += ["-c:a", "libopus"]
    if bitrate:
        args += ["-b:a", bitrate]
    elif ext in (".mp3", ".m4a", ".mp4", ".aac", ".opus", ".ogg"):
        args += ["-b:a", "192k"]
    return args


# A filtergraph can be passed inline, which every ffmpeg accepts, but the OS
# caps how long a single argument may be (~32 KB total on Windows). Past this
# many characters we hand ffmpeg a file instead.
INLINE_SAFE = 6000
INLINE_CEILING = 24000 if sys.platform == "win32" else 100000

_FILTER_FILE_OPT: str | bool | None = None


def filter_file_option() -> str | None:
    """Which option makes THIS ffmpeg read a filtergraph from a file.

    -filter_complex_script was deprecated in ffmpeg 7.0 ("use -/filter_complex
    <file> instead") and removed in ffmpeg 8, while -/filter_complex does not
    exist before 7.0. Rather than parse version strings, ask ffmpeg directly
    once and remember the answer. Returns None if neither is accepted.
    """
    global _FILTER_FILE_OPT
    if _FILTER_FILE_OPT is not None:
        return _FILTER_FILE_OPT or None

    chosen: str | None = None
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".filter", delete=False) as fh:
            fh.write("[0:a]anull[out]")
            path = fh.name
        for opt in ("-/filter_complex", "-filter_complex_script"):
            probe_cmd = [tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin",
                         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                         opt, path, "-map", "[out]", "-t", "0.05",
                         "-f", "null", "-"]
            if run(probe_cmd).returncode == 0:
                chosen = opt
                break
    except OSError:
        chosen = None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    _FILTER_FILE_OPT = chosen or False
    return chosen


def render(src: str, out: str, filtergraph: str, bitrate: str | None = None,
           overwrite: bool = True, expected: float = 0.0, on_progress=None,
           cancel=None, echo=None) -> None:
    script: str | None = None
    try:
        cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin",
               "-y" if overwrite else "-n",
               "-i", src]

        if len(filtergraph) <= INLINE_SAFE:
            cmd += ["-filter_complex", filtergraph]
        else:
            opt = filter_file_option()
            if opt:
                with tempfile.NamedTemporaryFile("w", suffix=".filter",
                                                 delete=False) as fh:
                    fh.write(filtergraph)
                    script = fh.name
                cmd += [opt, script]
                if echo:
                    echo(f"(filtergraph is {len(filtergraph)} chars — "
                         f"passing it via {opt})")
            elif len(filtergraph) <= INLINE_CEILING:
                cmd += ["-filter_complex", filtergraph]
            else:
                die("this ffmpeg build accepts neither -/filter_complex nor "
                    "-filter_complex_script, and the edit has too many separate "
                    "cuts to pass on the command line.\n"
                    "Raise --min-pause (or --pause-to) to make fewer cuts, "
                    "or update ffmpeg.")

        cmd += ["-map", "[out]", "-vn", "-sn", "-dn", "-map_metadata", "0"]
        cmd += encode_args(out, bitrate)
        cmd += [out]
        if echo:
            echo("ffmpeg " + " ".join(cmd[1:]))
        code, err = _stream_ffmpeg(cmd, expected, on_progress, cancel)
        if cancel is not None and cancel.is_set():
            if os.path.exists(out):
                try:
                    os.unlink(out)
                except OSError:
                    pass
            raise DeadAirError("cancelled")
        if code != 0:
            die(f"render failed\n{err.strip()[-3000:]}")
    finally:
        if script:
            try:
                os.unlink(script)
            except OSError:
                pass


# ---------------------------------------------------------------- jobs


@dataclass
class Options:
    """Everything that decides what an edit does, in one passable object."""
    min_pause: float = 1.0
    pause_to: float = 0.25
    threshold: float = -35.0
    no_cut: bool = False
    speed: float = 1.0
    pitch_shift: bool = False
    hq: bool = False
    bitrate: "str | None" = None
    overwrite: bool = True

    def validate(self) -> None:
        if self.speed <= 0:
            die("speed must be greater than 0")
        if self.min_pause <= 0:
            die("minimum pause length must be greater than 0")
        if self.pause_to < 0:
            die("the shortened pause length cannot be negative")
        if not self.no_cut and self.pause_to >= self.min_pause:
            die("the shortened pause length must be smaller than the minimum "
                "pause length, or nothing would be cut")
        if self.hq and self.pitch_shift:
            die("the high-quality stretch only applies to pitch-preserved speed")


@dataclass
class Result:
    """What happened to one file. Carries the failure instead of raising it."""
    src: str
    out: "str | None" = None
    duration: float = 0.0
    final: float = 0.0
    removed: float = 0.0
    pauses: list = field(default_factory=list)
    error: "str | None" = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def saved(self) -> float:
        return max(0.0, self.duration - self.final)

    @property
    def dead_air(self) -> float:
        return sum(e - s for s, e in self.pauses)


def analyze(src: str, opts: Options, on_progress=None, cancel=None):
    """Probe, detect the pauses, and work out what the edit would produce."""
    info = probe(src)
    pauses = detect_pauses(src, opts.threshold, opts.min_pause, info.duration,
                           on_progress=on_progress, cancel=cancel)
    if opts.no_cut:
        keeps, removed = [(0.0, info.duration)], 0.0
    else:
        keeps, removed = plan_keeps(pauses, opts.pause_to, info.duration)
    final = (info.duration - removed) / opts.speed
    return info, pauses, keeps, removed, final


# Detection is roughly a third of the work; rendering is the rest.
DETECT_SHARE = 0.35


def process_file(src: str, out: "str | None", opts: Options,
                 on_progress=None, cancel=None, echo=None) -> Result:
    """Run the whole pipeline for one file.

    Ordinary failures come back inside the Result rather than as exceptions,
    so one bad file in a batch never takes the batch down with it.
    """
    r = Result(src=src)
    try:
        opts.validate()
        share = DETECT_SHARE if out else 1.0
        info, pauses, keeps, removed, final = analyze(
            src, opts,
            on_progress=(lambda f: on_progress(f * share)) if on_progress else None,
            cancel=cancel)
        r.duration, r.pauses, r.removed, r.final = (
            info.duration, pauses, removed, final)

        if out:
            folder = os.path.dirname(os.path.abspath(out))
            if folder and not os.path.isdir(folder):
                os.makedirs(folder, exist_ok=True)
            graph = build_filtergraph(
                keeps,
                speed_filters(opts.speed, info.sample_rate, opts.pitch_shift,
                              opts.hq),
                cut=not opts.no_cut)
            render(src, out, graph, opts.bitrate, overwrite=opts.overwrite,
                   expected=final,
                   on_progress=(lambda f: on_progress(
                       DETECT_SHARE + f * (1 - DETECT_SHARE)))
                   if on_progress else None,
                   cancel=cancel, echo=echo)
            r.out = out
    except DeadAirError as exc:
        r.error = str(exc)
    except Exception as exc:                          # noqa: BLE001 - batch safety
        r.error = f"{type(exc).__name__}: {exc}"
    return r


# ------------------------------------------------------------ batch inputs

GLOB_CHARS = "*?["


def collect_inputs(patterns: "list[str]", recursive: bool = False,
                   exclude_dir: "str | None" = None
                   ) -> "tuple[list[str], list[str]]":
    """Expand files, folders and globs into a de-duplicated file list.

    Globs are expanded here rather than left to the shell, because cmd.exe and
    PowerShell do not expand them at all.

    `exclude_dir` keeps a run from eating its own output: pointing --out-dir
    inside the folder being scanned would otherwise re-process yesterday's
    results on the next pass.
    """
    files: "list[str]" = []
    missing: "list[str]" = []
    seen = set()

    blocked = None
    if exclude_dir and os.path.isdir(exclude_dir):
        blocked = os.path.normcase(os.path.abspath(exclude_dir)) + os.sep

    def add(path: str) -> None:
        full = os.path.abspath(path)
        key = os.path.normcase(full)
        if blocked and key.startswith(blocked):
            return
        if key not in seen and os.path.isfile(full):
            seen.add(key)
            files.append(full)

    for pat in patterns:
        if os.path.isdir(pat):
            if recursive:
                for root, _dirs, names in sorted(os.walk(pat)):
                    for n in sorted(names):
                        if n.lower().endswith(AUDIO_EXTS):
                            add(os.path.join(root, n))
            else:
                for n in sorted(os.listdir(pat)):
                    if n.lower().endswith(AUDIO_EXTS):
                        add(os.path.join(pat, n))
        elif any(c in pat for c in GLOB_CHARS):
            hits = sorted(glob.glob(pat, recursive=recursive))
            if not hits:
                missing.append(pat)
            for h in hits:
                add(h)
        elif os.path.isfile(pat):
            add(pat)
        else:
            missing.append(pat)

    return files, missing


def output_path(src: str, out_dir: "str | None", suffix: str,
                fmt: "str | None") -> str:
    """Where one source file's result should be written."""
    stem, ext = os.path.splitext(os.path.basename(src))
    if fmt:
        ext = "." + fmt.lstrip(".")
    elif not ext:
        ext = ".wav"
    folder = out_dir if out_dir else os.path.dirname(src)
    out = os.path.join(folder or ".", stem + suffix + ext)
    # Never let the output land on top of its own source.
    if os.path.normcase(os.path.abspath(out)) == os.path.normcase(
            os.path.abspath(src)):
        out = os.path.join(folder or ".", stem + "_tight" + ext)
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(
                os.path.abspath(src)):
            out = os.path.join(folder or ".", stem + "_tight2" + ext)
    return out


# ---------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deadair",
        description="Detect dead air, cut or shorten it, and retime the audio. "
                    "Accepts any number of files, folders or globs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples")[-1],
    )
    ap.add_argument("inputs", nargs="+", metavar="INPUT",
                    help="audio or video files, folders, or globs like *.mp3")
    ap.add_argument("-o", "--output",
                    help="output file — only valid with a single input")

    g = ap.add_argument_group("dead air")
    g.add_argument("--min-pause", type=float, default=1.0, metavar="SEC",
                   help="only pauses at least this long are touched (default 1.0)")
    g.add_argument("--pause-to", type=float, default=0.25, metavar="SEC",
                   help="shorten each of those pauses to this length; "
                        "0 removes them entirely (default 0.25)")
    g.add_argument("--threshold", type=float, default=-35.0, metavar="dB",
                   help="anything quieter than this counts as silence "
                        "(default -35; try -45 for a quiet room, -25 for noisy)")
    g.add_argument("--no-cut", action="store_true",
                   help="detect and report pauses but leave the audio uncut")

    g = ap.add_argument_group("speed")
    g.add_argument("--speed", type=float, default=1.0, metavar="X",
                   help="playback rate, decimals fine (1.25, 0.9). Default 1.0")
    g.add_argument("--pitch-shift", action="store_true",
                   help="let pitch follow the speed (tape/varispeed). "
                        "Default keeps the original pitch.")
    g.add_argument("--hq", action="store_true",
                   help="use librubberband instead of atempo for pitch-preserved "
                        "stretching — slower, cleaner on voice")

    g = ap.add_argument_group("batch")
    g.add_argument("-d", "--out-dir", metavar="DIR",
                   help="write every result here (created if needed). "
                        "Without it, results land beside their sources")
    g.add_argument("--suffix", default="_tight", metavar="STR",
                   help="appended to each output's name (default _tight)")
    g.add_argument("--format", metavar="EXT",
                   help="force every output to this extension, e.g. mp3")
    g.add_argument("-r", "--recursive", action="store_true",
                   help="descend into subfolders when given a folder or **/ glob")
    g.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                   help="process N files at once (default 1)")
    g.add_argument("--skip-existing", action="store_true",
                   help="leave files alone whose output already exists")

    g = ap.add_argument_group("output")
    g.add_argument("--bitrate", metavar="RATE", help="e.g. 192k (lossy formats)")
    g.add_argument("--dry-run", action="store_true",
                   help="report the pauses and the projected result, write nothing")
    g.add_argument("--json", metavar="FILE", help="write the pause list as JSON")
    g.add_argument("--list", action="store_true",
                   help="print every detected pause with timestamps")
    g.add_argument("-n", "--no-overwrite", action="store_true",
                   help="fail instead of overwriting the output")
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true",
                   help="echo the ffmpeg command")
    return ap


def _options_from(a: argparse.Namespace) -> Options:
    return Options(
        min_pause=a.min_pause, pause_to=a.pause_to, threshold=a.threshold,
        no_cut=a.no_cut, speed=a.speed, pitch_shift=a.pitch_shift, hq=a.hq,
        bitrate=a.bitrate, overwrite=not a.no_overwrite,
    )


def _json_for(r: Result, opts: Options) -> dict:
    return {
        "input": r.src,
        "output": r.out,
        "duration": round(r.duration, 3),
        "result_duration": round(r.final, 3),
        "removed": round(r.removed, 3),
        "threshold_db": opts.threshold,
        "min_pause": opts.min_pause,
        "pauses": [{"start": round(s, 3), "end": round(e, 3),
                    "duration": round(e - s, 3)} for s, e in r.pauses],
        "error": r.error,
    }


def _report_one(r: Result, opts: Options, a: argparse.Namespace, say) -> None:
    """The detailed single-file readout."""
    say(f"in   {r.src}  ·  {hms(r.duration)}")
    say(f"pauses  {len(r.pauses)} at or over {opts.min_pause}s "
        f"(below {opts.threshold:g} dB)  ·  {hms(r.dead_air)} of dead air")
    if a.list or a.dry_run:
        for i, (s, e) in enumerate(r.pauses, 1):
            say(f"  {i:>4}  {hms(s)} → {hms(e)}   {e - s:6.2f}s")
    say(f"cut  {hms(r.removed)} removed  →  {hms(r.duration - r.removed)}")
    if abs(opts.speed - 1.0) > 1e-9:
        mode = "pitch shifts with it" if opts.pitch_shift else (
            "pitch preserved, rubberband" if opts.hq else "pitch preserved")
        say(f"speed {opts.speed:g}x ({mode})  →  {hms(r.final)}")
    delta = (1 - r.final / r.duration) * 100 if r.duration else 0.0
    say(f"out  {hms(r.final)} total, "
        f"{abs(delta):.1f}% {'shorter' if delta >= 0 else 'longer'} than the source")


def run_cli(a: argparse.Namespace) -> int:
    missing_tools = have_ffmpeg()
    if missing_tools:
        die(f"{' and '.join(missing_tools)} not found. Install ffmpeg and put it "
            f"on PATH, or drop {'/'.join(missing_tools)} next to "
            f"{'this app' if getattr(sys, 'frozen', False) else 'deadair.py'}.")

    opts = _options_from(a)
    opts.validate()
    if a.hq and not has_rubberband():
        die("this ffmpeg has no rubberband filter; drop --hq")
    if a.jobs < 1:
        die("--jobs must be at least 1")

    files, missing = collect_inputs(a.inputs, a.recursive, a.out_dir)
    for m in missing:
        print(f"warning: nothing matched {m}", file=sys.stderr)
    if not files:
        die("no input files found")
    if a.output and len(files) > 1:
        die(f"-o/--output takes a single input, but {len(files)} files matched. "
            "Use --out-dir for batches.")

    say = (lambda *x: None) if a.quiet else (lambda *x: print(*x))
    echo = (lambda m: print(m, file=sys.stderr)) if a.verbose else None

    # Work out every destination up front so collisions surface before any work.
    targets: "list[tuple[str, str | None]]" = []
    skipped = 0
    for src in files:
        if a.dry_run:
            targets.append((src, None))
            continue
        out = a.output if a.output else output_path(
            src, a.out_dir, a.suffix, a.format)
        if a.skip_existing and os.path.exists(out):
            skipped += 1
            say(f"skip  {os.path.basename(out)} (already exists)")
            continue
        targets.append((src, out))

    if not targets:
        say("nothing to do — every output already exists")
        return 0

    single = len(targets) == 1
    if not single:
        say(f"{len(targets)} files" + (f", {a.jobs} at a time" if a.jobs > 1 else "")
            + (f" → {a.out_dir}" if a.out_dir else ""))

    results: "list[Result]" = []

    def work(item):
        src, out = item
        return process_file(src, out, opts, echo=echo)

    if a.jobs > 1 and len(targets) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            futures = {pool.submit(work, t): t for t in targets}
            done = 0
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                done += 1
                say(_line_for(r, done, len(targets)))
    else:
        for i, item in enumerate(targets, 1):
            if single and not a.quiet:
                r = work(item)
                results.append(r)
                if r.ok:
                    _report_one(r, opts, a, say)
                    if r.out:
                        say(f"wrote {r.out}")
                    elif a.dry_run:
                        say("dry run — nothing written")
                else:
                    print(f"error: {r.error}", file=sys.stderr)
            else:
                r = work(item)
                results.append(r)
                say(_line_for(r, i, len(targets)))

    if a.json:
        payload = (_json_for(results[0], opts) if single
                   else {"files": [_json_for(r, opts) for r in results]})
        with open(a.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        say(f"json {a.json}")

    failed = [r for r in results if not r.ok]
    if not single:
        ok = [r for r in results if r.ok]
        total_in = sum(r.duration for r in ok)
        total_out = sum(r.final for r in ok)
        say("")
        say(f"{len(ok)} done" + (f", {len(failed)} failed" if failed else "")
            + (f", {skipped} skipped" if skipped else ""))
        if ok:
            pct = (1 - total_out / total_in) * 100 if total_in else 0.0
            say(f"{hms(total_in)} in  →  {hms(total_out)} out   "
                f"({hms(total_in - total_out)} saved, {pct:.1f}%)")
        for r in failed:
            print(f"  failed: {os.path.basename(r.src)} — "
                  f"{r.error.splitlines()[0] if r.error else '?'}", file=sys.stderr)

    return 1 if failed else 0


def _line_for(r: Result, i: int, total: int) -> str:
    tag = f"[{i}/{total}]"
    name = os.path.basename(r.src)
    if not r.ok:
        return f"{tag} {name}  FAILED  {r.error.splitlines()[0] if r.error else ''}"
    pct = (1 - r.final / r.duration) * 100 if r.duration else 0.0
    n = len(r.pauses)
    return (f"{tag} {name}  {hms(r.duration)} → {hms(r.final)}  "
            f"({pct:.0f}% shorter, {n} pause{'' if n == 1 else 's'})")


def main() -> None:
    try:
        sys.exit(run_cli(build_parser().parse_args()))
    except DeadAirError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # Piping into head/less closes stdout early; exit quietly instead of
        # dumping a traceback.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
