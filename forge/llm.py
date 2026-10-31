"""Talks to Claude through the Claude Code CLI in headless mode.

Uses your Claude subscription (whatever `claude` is logged into) — no API key.
"""
import json
import os
import re
import shutil
import subprocess


class ClaudeError(RuntimeError):
    pass


def find_exe(name: str) -> str | None:
    """On Windows, npm installs an extensionless shell script next to the .cmd; only the .cmd/.exe can be run."""
    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat"):
            hit = shutil.which(name + ext)
            if hit:
                return hit
        return None
    return shutil.which(name)


def find_claude() -> str:
    exe = find_exe("claude")
    if not exe:
        raise ClaudeError(
            "Couldn't find the `claude` command. Install Claude Code and log in "
            "(`npm i -g @anthropic-ai/claude-code`, then run `claude` once)."
        )
    return exe


def ask(prompt: str, model: str | None = None, timeout: int = 600, tools: list[str] | None = None,
        cwd: str | None = None) -> str:
    """Send a prompt, return Claude's text reply. Prompt goes over stdin (Windows has a short command-line limit).

    tools: Claude Code tools Claude may use while answering (e.g. ["Read"] to look at image files).
    """
    args = [find_claude(), "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    if tools:
        args += ["--allowedTools", ",".join(tools)]
    proc = subprocess.run(
        args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout, cwd=cwd
    )
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip()
        try:
            msg = json.loads(proc.stdout).get("result") or msg
        except (json.JSONDecodeError, AttributeError):
            pass
        if "login" in msg.lower():
            msg += "  ->  run `claude`, type /login, choose your Claude subscription account, then /exit"
        raise ClaudeError(f"Claude Code: {msg}")
    try:
        data = json.loads(proc.stdout)
        if data.get("is_error"):
            raise ClaudeError(str(data.get("result")))
        return data.get("result", "")
    except json.JSONDecodeError:
        return proc.stdout


def ask_json(prompt: str, model: str | None = None, retries: int = 2, **kw) -> dict | list:
    last = ""
    for attempt in range(retries + 1):
        p = prompt if attempt == 0 else (
            prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object, no prose, no code fences."
        )
        last = ask(p, model=model, **kw)
        parsed = extract_json(last)
        if parsed is not None:
            return parsed
    raise ClaudeError(f"Claude didn't return valid JSON. Last reply started with: {last[:300]!r}")


def extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
