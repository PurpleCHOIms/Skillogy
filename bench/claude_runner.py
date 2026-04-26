"""Runs real Claude Code CLI to measure skill trigger rate.

Two conditions:
  claude_native  — real Claude Code, SKILL_ROUTER_DISABLE=1, no hook
  claude_hook    — real Claude Code, hook injected via --settings (our service active)

Detection strategy (in priority order):
  1. Skill tool call: stream-json tool_use block with name="Skill" and input.skill == gold
  2. Text mention: Claude's text response contains the skill name as a word/phrase

Timeout: 60s per query (configurable via BENCH_CLAUDE_TIMEOUT env var).
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
DEFAULT_TIMEOUT = int(os.environ.get("BENCH_CLAUDE_TIMEOUT", "60"))


def _load_dotenv() -> dict[str, str]:
    """Read .env at project root and return key=value pairs (no os.environ mutation)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_DOTENV = _load_dotenv()
# Both conditions cwd here so testbed skills are visible (project-level skills)
BENCH_CWD = os.environ.get("BENCH_CWD", str(Path.home() / "skill-router-testbed"))

# Disable all user-level plugins (OMC etc.) so SessionStart hooks don't slow runs.
_DISABLED_PLUGINS = {
    "enabledPlugins": {
        "claude-hud@claude-hud": False,
        "oh-my-claudecode@omc": False,
        "context7@claude-plugins-official": False,
        "typescript-lsp@claude-plugins-official": False,
        "vercel@claude-plugins-official": False,
        "pyright-lsp@claude-plugins-official": False,
        "ouroboros@ouroboros": False,
        "ui-ux-pro-max@ui-ux-pro-max-skill": False,
        "superpowers@claude-plugins-official": False,
    }
}


def run_claude_query(
    query: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run `claude --print --output-format stream-json <query>`.

    Returns dict:
      {
        "stdout": str,          # raw stdout
        "stderr": str,          # raw stderr
        "returncode": int,
        "latency_ms": float,
        "skill_calls": list[str],   # skill names from Skill tool calls
        "text": str,            # plain text response (concatenated text deltas)
        "error": str | None,    # exception message if subprocess failed
      }
    """
    settings = json.dumps({**_DISABLED_PLUGINS, "hooks": {"UserPromptSubmit": []}})
    env = {**os.environ, **_DOTENV}
    env["SKILL_ROUTER_DISABLE"] = "1"  # double-safety: suppress hook even if it somehow fires
    return _stream_until_skill(
        [
            CLAUDE_BIN, "--settings", settings,
            "--print", "--verbose", "--output-format", "stream-json", query,
        ],
        env=env,
        cwd=BENCH_CWD,
        timeout=timeout,
    )


_HOOK_SCRIPT = str(Path(__file__).parent.parent / "scripts" / "skill-router-hook.sh")


def run_claude_query_with_hook(
    query: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run Claude Code with our hook injected via --settings.

    The hook fires on UserPromptSubmit, injects skill hint as additionalContext.
    We then detect if Claude called the right Skill tool.
    """
    settings = json.dumps({
        **_DISABLED_PLUGINS,
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": _HOOK_SCRIPT}]}
            ]
        },
    })
    env = {**os.environ, **_DOTENV}
    env.pop("SKILL_ROUTER_DISABLE", None)  # ensure hook is NOT suppressed
    return _stream_until_skill(
        [
            CLAUDE_BIN, "--settings", settings,
            "--print", "--verbose", "--output-format", "stream-json", query,
        ],
        env=env,
        cwd=BENCH_CWD,
        timeout=timeout,
    )


def _stream_until_skill(
    cmd: list[str], env: dict, timeout: int, cwd: str | None = None,
) -> dict:
    """Run Claude Code CLI, streaming stdout. Kill subprocess as soon as a
    Skill tool call is detected — no need to wait for the full response."""
    start = time.perf_counter()
    skill_calls: list[str] = []
    text_parts: list[str] = []
    stdout_chunks: list[str] = []
    error: str | None = None
    returncode = -1

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
            bufsize=1,
        )
    except FileNotFoundError:
        return {
            "stdout": "", "stderr": "", "returncode": -1,
            "latency_ms": (time.perf_counter() - start) * 1000,
            "skill_calls": [], "text": "",
            "error": f"claude binary not found: {cmd[0]}",
        }

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if (time.perf_counter() - start) > timeout:
                error = f"timeout after {timeout}s"
                break
            stdout_chunks.append(line)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            _consume_event(event, skill_calls, text_parts)
            if skill_calls:
                break
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        returncode = proc.returncode
    except Exception as exc:
        error = str(exc)
        try:
            proc.kill()
        except Exception:
            pass

    latency_ms = (time.perf_counter() - start) * 1000
    return {
        "stdout": "".join(stdout_chunks),
        "stderr": "",
        "returncode": returncode,
        "latency_ms": latency_ms,
        "skill_calls": skill_calls,
        "text": "".join(text_parts),
        "error": error,
    }


def _consume_event(event: dict, skill_calls: list[str], text_parts: list[str]) -> None:
    """Mutate skill_calls/text_parts based on a single stream-json event."""
    etype = event.get("type", "")

    def _add(name: str, inp: dict) -> None:
        if name == "Skill":
            s = inp.get("skill", "")
            if s and s.lower() not in skill_calls:
                skill_calls.append(s.lower())

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                _add(block.get("name", ""), block.get("input", {}))
    elif etype == "tool_use":
        _add(event.get("name", ""), event.get("input", {}))
    elif etype == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            _add(block.get("name", ""), block.get("input", {}))
    elif etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text_parts.append(delta.get("text", ""))
    elif etype == "result":
        result_text = event.get("result", "")
        if isinstance(result_text, str):
            text_parts.append(result_text)


def _parse_stream_json(raw: str) -> tuple[list[str], str]:
    """Parse stream-json output. Returns (skill_names_called, plain_text)."""
    skill_calls: list[str] = []
    text_parts: list[str] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        def _extract_skill(name: str, inp: dict) -> None:
            if name == "Skill":
                s = inp.get("skill", "")
                if s and s.lower() not in skill_calls:
                    skill_calls.append(s.lower())

        # assistant message: full assembled content (primary source in stream-json)
        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    _extract_skill(block.get("name", ""), block.get("input", {}))

        # Fallback: top-level tool_use event
        if etype == "tool_use":
            _extract_skill(event.get("name", ""), event.get("input", {}))

        # Fallback: content_block_start (input may be partial here)
        if etype == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                _extract_skill(block.get("name", ""), block.get("input", {}))

        # Text deltas
        if etype == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))

        # Final message result
        if etype == "result":
            result_text = event.get("result", "")
            if isinstance(result_text, str):
                text_parts.append(result_text)

    return skill_calls, "".join(text_parts)


def detect_trigger(result: dict, gold: str) -> bool:
    """Detect if gold skill was triggered.

    Priority:
    1. Exact Skill tool call with gold name
    2. Skill tool call with gold name as substring
    3. Text response contains skill name as a token (loose match)
    """
    gold_norm = gold.lower().strip()

    # 1. Exact skill tool call
    if gold_norm in result["skill_calls"]:
        return True

    # 2. Partial skill name in tool calls
    for called in result["skill_calls"]:
        if gold_norm in called or called in gold_norm:
            return True

    # 3. Text response contains skill name (word boundary match)
    text = result["text"].lower()
    # Match skill name as a recognizable token (hyphenated names)
    pattern = re.escape(gold_norm).replace(r"\-", r"[\-_\s]")
    if re.search(pattern, text):
        return True

    return False


def run_hook_directly(query: str) -> tuple[str, float]:
    """Run hook.py directly (without full Claude Code) to get its skill suggestion.

    Returns (skill_name_suggested, latency_ms). skill_name is "" if no match.
    This is cheaper than full claude CLI and useful for hook-only accuracy.
    """
    payload = json.dumps(
        {
            "prompt": query,
            "hook_event_name": "UserPromptSubmit",
            "session_id": "bench",
            "cwd": str(Path.cwd()),
            "transcript_path": "",
            "permission_mode": "default",
        }
    )

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "skill_router.adapters.hook"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        output = json.loads(proc.stdout.strip() or "{}")
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        # Parse skill name from: "`skill-name` (score=X.XX)"
        m = re.search(r"`([^`]+)`\s*\(score=", context)
        skill_name = m.group(1).lower() if m else ""
        return skill_name, latency_ms
    except Exception:
        latency_ms = (time.perf_counter() - start) * 1000
        return "", latency_ms
