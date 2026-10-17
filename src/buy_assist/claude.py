"""One headless Claude call through `claude-json`, which returns schema-shaped JSON.

Callers pass untrusted text in the prompt, so `claude-json` runs Claude with no tools. The
schema constrains only the answer's shape: callers check every field's meaning.
"""

import json
import subprocess
from collections.abc import Callable
from typing import Any

type Runner = Callable[[str, dict[str, Any]], dict[str, Any]]
"""Ask Claude `prompt`, getting back an object shaped by the JSON schema."""

_TIMEOUT_SECONDS = 120


class ParseError(Exception):
    """Claude couldn't turn the text into what was asked for."""


def claude_json(system_prompt: str) -> Runner:
    def run(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return _run_claude_json(system_prompt, prompt, schema)

    return run


def _run_claude_json(system_prompt: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "claude-json",
                "--schema",
                json.dumps(schema),
                "--system-prompt",
                system_prompt,
                "--timeout",
                str(_TIMEOUT_SECONDS),
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS + 10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ParseError(f"claude-json did not run: {type(e).__name__}") from e
    if result.returncode != 0:
        raise ParseError(f"claude-json exited {result.returncode}: {result.stderr.strip()[:200]}")
    try:
        answer = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ParseError("claude-json printed invalid JSON") from e
    if not isinstance(answer, dict):
        raise ParseError("claude-json answer is not an object")
    return answer  # pyright: ignore[reportUnknownVariableType]
