"""Daily task printout: curate via `claude -p`, render with Pillow, print to a Phomemo M02 Pro over BLE.

Orchestrator only — the rendering and the BLE driver live in render.py
and phomemo.py respectively. phomemo.py is fully standalone (Pillow +
Bleak only) and can be lifted into other projects unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import get_args

import phomemo
from render import Brief, Priority, RemainingTask, TopPick, render

REPO_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Task-system config (env-driven; both required)
# ---------------------------------------------------------------------------
# This script is invariant of which task system / MCP server you use. The
# two task-system-specific bits live entirely in env vars; the script errors
# loudly if either is missing.
#
# - TASKMEMO_MCP_TOOL: the fully-qualified MCP tool name, "mcp__<server>__<tool>".
#   The server must be reachable to `claude -p` (project `.mcp.json`,
#   user-level config, or via TASKMEMO_MCP_CONFIG, see _call_claude_once).
#
# - TASKMEMO_PROMPT_FILE: path to the system prompt file. Relative paths
#   resolve against this repo's root. Contents are passed verbatim to
#   `claude --system-prompt`; a starting template sits in `prompt.example.md`.
#   You hardcode your tool name in the prompt text; the script does not
#   template it for you.

USER_PROMPT = "Fetch today's tasks and emit the curated brief JSON."


def _load_system_prompt() -> str:
    prompt_file = os.environ.get("TASKMEMO_PROMPT_FILE")
    if not prompt_file:
        raise RuntimeError(
            "TASKMEMO_PROMPT_FILE is not set. Set it in your .env (or shell env) "
            "to the path of your system prompt file. Start from "
            "`prompt.example.md` if you don't have one yet."
        )
    path = Path(prompt_file)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise RuntimeError(
            f"System prompt file not found: {path}\n"
            f"TASKMEMO_PROMPT_FILE points at a file that doesn't exist. Copy "
            f"`prompt.example.md` to `prompt.md` (or wherever) and update "
            f"TASKMEMO_PROMPT_FILE to match."
        )
    return path.read_text()


def _require_mcp_tool() -> str:
    mcp_tool = os.environ.get("TASKMEMO_MCP_TOOL")
    if not mcp_tool:
        raise RuntimeError(
            "TASKMEMO_MCP_TOOL is not set. Set it in your .env (or shell env) "
            "to the fully-qualified MCP tool name "
            "(e.g. 'mcp__myserver__list_tasks')."
        )
    return mcp_tool


PRIORITY_ENUM = {"type": "string", "enum": list(get_args(Priority))}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "topPicks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "priority": PRIORITY_ENUM,
                },
                "required": ["title", "why", "priority"],
            },
        },
        "remainingTasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "meta": {"type": "string"},
                    "priority": PRIORITY_ENUM,
                },
                "required": ["title", "meta", "priority"],
            },
        },
    },
    "required": ["date", "topPicks", "remainingTasks"],
}


def _call_claude_once() -> dict:
    # We don't bundle a `.mcp.json`. The user wires up their MCP server via
    # one of:
    #   - TASKMEMO_MCP_CONFIG env var pointing at a JSON file or inline JSON
    #     string (passed verbatim to `claude --mcp-config`). With this set we
    #     also pass --strict-mcp-config so only that one server starts (fast).
    #   - Or registered globally with Claude Code (slower, since `claude -p`
    #     will spawn all configured servers on startup).
    mcp_tool = _require_mcp_tool()
    system_prompt = _load_system_prompt()
    cmd = [
        "claude",
        "-p",
        USER_PROMPT,
        "--output-format", "json",
        "--system-prompt", system_prompt,
        "--json-schema", json.dumps(OUTPUT_SCHEMA),
        "--allowedTools", mcp_tool,
        "--no-session-persistence",
        "--model", os.environ.get("CURATE_MODEL", "opus"),
    ]
    mcp_config = os.environ.get("TASKMEMO_MCP_CONFIG")
    if mcp_config:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    timeout_s = float(os.environ.get("TASKMEMO_CLAUDE_TIMEOUT", "120"))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        raise RuntimeError(
            "`claude` not found on PATH. Install Claude Code "
            "(https://claude.com/claude-code) and ensure the CLI is on your PATH."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"`claude -p` timed out after {timeout_s:.0f}s. The MCP server is most likely "
            f"hung — common cause is expired credentials in a wrapping command "
            f"(e.g. `op run`). Try invoking the server's command directly to verify, "
            f"or raise the limit via TASKMEMO_CLAUDE_TIMEOUT."
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {result.returncode}\n"
            f"stderr:\n{result.stderr}\n"
            f"stdout (first 500B):\n{result.stdout[:500]}"
        )
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse claude envelope: {e}\nstdout:\n{result.stdout[:500]}")
    if envelope.get("is_error"):
        raise RuntimeError(f"claude returned error: {envelope.get('error') or envelope.get('subtype')}")

    output = envelope.get("structured_output")
    if not output:
        text = (envelope.get("result") or "").strip()
        try:
            output = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"No structured_output in claude response. Raw:\n{result.stdout[:500]}")
    return output


def curate(max_retries: int = 2) -> Brief:
    # MCP servers can take several seconds to start. If Claude's first call
    # lands before the server is ready, the tool returns an error and Claude
    # emits an empty brief — or `claude -p` raises mid-stream. Retry up to
    # max_retries+1 times in either case.
    last_output: dict | None = None
    for attempt in range(max_retries + 1):
        try:
            output = _call_claude_once()
        except Exception as e:
            if attempt >= max_retries:
                raise
            print(
                f"  curate failed ({type(e).__name__}: {e}; "
                f"attempt {attempt + 1}/{max_retries + 1}); retrying…",
                file=sys.stderr,
            )
            time.sleep(2)
            continue
        last_output = output
        if output.get("topPicks") or output.get("remainingTasks"):
            break
        if attempt < max_retries:
            print(
                f"  curate returned empty brief (attempt {attempt + 1}/{max_retries + 1}); retrying…",
                file=sys.stderr,
            )
            time.sleep(2)

    assert last_output is not None
    return Brief(
        date=last_output["date"],
        top_picks=[TopPick(**p) for p in last_output["topPicks"]],
        remaining_tasks=[RemainingTask(**t) for t in last_output["remainingTasks"]],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily task printout via an MCP tool and a Phomemo M02 Pro over BLE.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="skip BLE print; save rendered PNG to /tmp/print-day.png (override with --save-png)",
    )
    parser.add_argument("--debug", action="store_true", help="enable verbose BLE / print logging")
    parser.add_argument("--save-png", help="save rendered PNG to this path")
    parser.add_argument("--device", default=os.environ.get("PHOMEMO_DEVICE_NAME", "M02 Pro"))
    args = parser.parse_args()

    # Always save the rendered PNG so you can verify what was just printed.
    save_png = args.save_png or "/tmp/print-day.png"

    print("Curating today's brief with Claude…")
    brief = curate()
    print(f"  {brief.date}: {len(brief.top_picks)} top pick(s), {len(brief.remaining_tasks)} more.")

    print("Rendering to PNG…")
    image = render(brief)
    print(f"  {image.size[0]}x{image.size[1]} px")

    save_path = Path(save_png)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(save_path)
    print(f"Saved PNG to {save_path}")

    if args.dry_run:
        print("Dry run — skipping print.")
        return 0

    chunk_env = os.environ.get("PHOMEMO_CHUNK_SIZE")
    chunk_size = int(chunk_env) if chunk_env else None
    chunk_delay_ms = float(os.environ.get("PHOMEMO_CHUNK_DELAY_MS", "40"))
    debug = args.debug or bool(os.environ.get("PRINT_DEBUG"))

    print("Connecting to printer…")
    phomemo.print_image(
        image,
        device_name=args.device,
        chunk_size=chunk_size,
        chunk_delay_ms=chunk_delay_ms,
        debug=debug,
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
