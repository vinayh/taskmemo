"""Daily task printout: curate via `claude -p`, render with Pillow, print to a Phomemo M02 Pro over BLE."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from bleak import BleakClient, BleakScanner

REPO_ROOT = Path(__file__).resolve().parent
FONTS_DIR = REPO_ROOT / "fonts"

PRINT_WIDTH = 560
BYTES_PER_LINE = PRINT_WIDTH // 8

Priority = Literal["high", "medium", "low", "none"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TopPick:
    title: str
    why: str
    priority: Priority


@dataclass
class RemainingTask:
    title: str
    meta: str
    priority: Priority


@dataclass
class Brief:
    date: str
    top_picks: list[TopPick]
    remaining_tasks: list[RemainingTask]


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

MCP_TOOL = os.environ.get("TASKMEMO_MCP_TOOL")

PROMPT_FILE = os.environ.get("TASKMEMO_PROMPT_FILE")

USER_PROMPT = "Fetch today's tasks and emit the curated brief JSON."


def _load_system_prompt() -> str:
    if not PROMPT_FILE:
        raise RuntimeError(
            "TASKMEMO_PROMPT_FILE is not set. Set it in your .env (or shell env) "
            "to the path of your system prompt file. Start from "
            "`prompt.example.md` if you don't have one yet."
        )
    path = Path(PROMPT_FILE)
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
    if not MCP_TOOL:
        raise RuntimeError(
            "TASKMEMO_MCP_TOOL is not set. Set it in your .env (or shell env) "
            "to the fully-qualified MCP tool name "
            "(e.g. 'mcp__myserver__list_tasks')."
        )
    return MCP_TOOL

PRIORITY_ENUM = {"type": "string", "enum": ["high", "medium", "low", "none"]}

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
    result = subprocess.run(cmd, capture_output=True, text=True)
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
    # emits an empty brief. Retry up to max_retries+1 times if we get an
    # empty result.
    last_output: dict | None = None
    for attempt in range(max_retries + 1):
        output = _call_claude_once()
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
# Render with Pillow
# ---------------------------------------------------------------------------


PADDING_X = 20
PADDING_Y = 24
PRIORITY_DOT = 8
PRIORITY_DOT_GAP = 3
PRIORITY_WIDTH = 3 * PRIORITY_DOT + 2 * PRIORITY_DOT_GAP
NUMBER_COL_WIDTH = 36
CHECKBOX_SIZE = 22


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word]) if current else word
        if _text_w(draw, candidate, font) <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_priority(draw: ImageDraw.ImageDraw, x: int, y: int, priority: str) -> None:
    filled = {"high": 3, "medium": 2, "low": 1}.get(priority, 0)
    for i in range(3):
        cx = x + i * (PRIORITY_DOT + PRIORITY_DOT_GAP)
        if i < filled:
            draw.ellipse([(cx, y), (cx + PRIORITY_DOT, y + PRIORITY_DOT)], fill=0)


def _draw_dashed_line(draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int, dash: int = 6, gap: int = 4) -> None:
    x = x1
    while x < x2:
        end = min(x + dash, x2)
        draw.line([(x, y), (end, y)], fill=0, width=1)
        x += dash + gap


def _format_day(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%A")


def render(brief: Brief) -> Image.Image:
    canvas = Image.new("L", (PRINT_WIDTH, 6000), color=255)
    draw = ImageDraw.Draw(canvas)

    f_day = _font("Inter-Bold.ttf", 36)
    f_iso = _font("Inter-Regular.ttf", 22)
    f_section = _font("Inter-Bold.ttf", 18)
    f_pick_title = _font("Inter-Bold.ttf", 24)
    f_pick_why = _font("Inter-Regular.ttf", 18)
    f_task = _font("Inter-Regular.ttf", 22)
    f_meta = _font("Inter-Regular.ttf", 18)

    y = PADDING_Y
    content_right = PRINT_WIDTH - PADDING_X

    # Date header: day name left, ISO right
    day_name = _format_day(brief.date)
    day_bbox = draw.textbbox((PADDING_X, y), day_name, font=f_day)
    draw.text((PADDING_X, y), day_name, font=f_day, fill=0)

    iso_w = _text_w(draw, brief.date, f_iso)
    iso_bbox = draw.textbbox((0, 0), brief.date, font=f_iso)
    iso_h = iso_bbox[3] - iso_bbox[1]
    day_h = day_bbox[3] - day_bbox[1]
    iso_y = y + (day_h - iso_h) - 2
    draw.text((content_right - iso_w, iso_y), brief.date, font=f_iso, fill=0)

    y = day_bbox[3] + 6
    draw.rectangle([(PADDING_X, y), (content_right, y + 3)], fill=0)
    y += 3 + 18

    # TOP section
    if brief.top_picks:
        draw.text((PADDING_X, y), "T O P", font=f_section, fill=0)
        y += 24

        checkbox_x = PADDING_X
        number_x = checkbox_x + CHECKBOX_SIZE + 8
        priority_x = number_x + NUMBER_COL_WIDTH
        title_x = priority_x + PRIORITY_WIDTH + 10

        for i, pick in enumerate(brief.top_picks, 1):
            row_top = y
            box_top = row_top + 6
            draw.rectangle(
                [(checkbox_x, box_top), (checkbox_x + CHECKBOX_SIZE, box_top + CHECKBOX_SIZE)],
                outline=0, width=2,
            )
            num = f"{i}."
            draw.text((number_x, row_top), num, font=f_pick_title, fill=0)
            _draw_priority(draw, priority_x, row_top + 11, pick.priority)

            title_w = content_right - title_x
            wrapped_title = _wrap(draw, pick.title, f_pick_title, title_w)
            line_h = f_pick_title.size + 6
            for j, line in enumerate(wrapped_title):
                draw.text((title_x, row_top + j * line_h), line, font=f_pick_title, fill=0)
            y = row_top + len(wrapped_title) * line_h

            if pick.why:
                why_w = content_right - title_x
                wrapped_why = _wrap(draw, pick.why, f_pick_why, why_w)
                why_line_h = f_pick_why.size + 4
                for line in wrapped_why:
                    draw.text((title_x, y), line, font=f_pick_why, fill=0)
                    y += why_line_h
            y += 10

    # ALL TASKS section
    if brief.remaining_tasks:
        y += 10
        _draw_dashed_line(draw, PADDING_X, y, content_right)
        y += 14
        draw.text((PADDING_X, y), "A L L   T A S K S", font=f_section, fill=0)
        y += 24

        title_x = PADDING_X + CHECKBOX_SIZE + 8 + PRIORITY_WIDTH + 10

        for t in brief.remaining_tasks:
            row_top = y
            box_top = row_top + 4
            draw.rectangle(
                [(PADDING_X, box_top), (PADDING_X + CHECKBOX_SIZE, box_top + CHECKBOX_SIZE)],
                outline=0, width=2,
            )
            _draw_priority(draw, PADDING_X + CHECKBOX_SIZE + 8, row_top + 11, t.priority)

            meta_w = _text_w(draw, t.meta, f_meta) if t.meta else 0
            title_max_w = content_right - title_x - (meta_w + 12 if meta_w else 0)
            wrapped = _wrap(draw, t.title, f_task, max(60, title_max_w))
            line_h = f_task.size + 6
            for j, line in enumerate(wrapped):
                draw.text((title_x, row_top + j * line_h), line, font=f_task, fill=0)

            if t.meta:
                meta_y = row_top + (line_h - f_meta.size) // 2
                draw.text((content_right - meta_w, meta_y), t.meta, font=f_meta, fill=0)

            y = row_top + len(wrapped) * line_h + 4

    y += PADDING_Y

    # Crop to content height
    canvas = canvas.crop((0, 0, PRINT_WIDTH, y))
    # Threshold to 1-bit (0 = black, 255 = white)
    canvas = canvas.point(lambda p: 0 if p < 170 else 255, mode="1")
    return canvas


# ---------------------------------------------------------------------------
# ESC/POS encoder
# ---------------------------------------------------------------------------


def encode_escpos(image: Image.Image) -> bytes:
    """Encode a 1-bit Pillow image to Phomemo ESC/POS bytes.

    Matches https://github.com/vivier/phomemo-tools/blob/master/tools/phomemo-filter.py
    which is the upstream reverse-engineering reference for the M02 family.
    """
    if image.mode != "1":
        image = image.convert("1")
    width, height = image.size
    if width != PRINT_WIDTH:
        raise ValueError(f"image width {width} != {PRINT_WIDTH}")
    pixels = image.load()

    out = bytearray()
    # Header: ESC @, ESC a 0x01 (CENTER align — vivier uses center, not left),
    # then proprietary 1f 11 02 04.
    out += b"\x1b\x40\x1b\x61\x01\x1f\x11\x02\x04"

    line = 0
    remaining = height
    while remaining > 0:
        lines = min(remaining, 256)
        # GS v 0: 0x1d 0x76 0x30 m=0 xL xH yL yH
        out += b"\x1d\x76\x30\x00"
        out += BYTES_PER_LINE.to_bytes(2, "little")
        out += (lines - 1).to_bytes(2, "little")
        for _ in range(lines):
            for x in range(BYTES_PER_LINE):
                byte = 0
                for bit in range(8):
                    px_x = x * 8 + bit
                    if px_x < width and pixels[px_x, line] == 0:
                        byte |= 1 << (7 - bit)
                # 0x0a alone is interpreted as LF by the printer; 0x14 prints
                # the same bit pattern without that side-effect.
                if byte == 0x0a:
                    byte = 0x14
                out.append(byte)
            line += 1
        remaining -= lines

    # Footer: feed twice, then proprietary trigger sequence.
    out += b"\x1b\x64\x02\x1b\x64\x02"
    out += b"\x1f\x11\x08\x1f\x11\x0e\x1f\x11\x07\x1f\x11\x09"
    return bytes(out)


# ---------------------------------------------------------------------------
# BLE print via SimplePyBLE
# ---------------------------------------------------------------------------


PHOMEMO_DATA_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"


async def _print_async(data: bytes, device_name: str, debug: bool) -> None:
    if debug:
        print(f"[print] scanning for '{device_name}'…", file=sys.stderr)
    device = await BleakScanner.find_device_by_name(device_name, timeout=15.0)
    if device is None:
        raise RuntimeError(
            f"BLE device '{device_name}' not found within 15s. Is it powered on, "
            f"and does the terminal have Bluetooth permission "
            f"(System Settings → Privacy & Security → Bluetooth)?"
        )

    if debug:
        print(f"[print] connecting to {device.name} ({device.address})", file=sys.stderr)
        head = " ".join(f"{b:02x}" for b in data[:32])
        tail = " ".join(f"{b:02x}" for b in data[-32:])
        print(f"[print] head: {head}", file=sys.stderr)
        print(f"[print] tail: {tail}", file=sys.stderr)

    async with BleakClient(device) as client:
        if debug:
            print(f"[print] connected (mtu={client.mtu_size})", file=sys.stderr)

        # Locate the Phomemo data characteristic (ff02 short UUID under
        # service ff00). Bleak surfaces full UUIDs.
        target_char = None
        for service in client.services:
            for char in service.characteristics:
                if char.uuid.lower() == PHOMEMO_DATA_CHAR or "ff02" in char.uuid.lower():
                    target_char = char
                    break
            if target_char:
                break
        if target_char is None:
            raise RuntimeError("Phomemo write characteristic ff02 not found on this device")

        # Chunk to (negotiated MTU - 3 ATT header bytes) with a 40 ms pause
        # between chunks. That's ~6 KB/s feed, well below the M02 Pro's
        # ~12 KB/s nominal print rate — the buffer drains continuously and
        # the tail of the stream isn't clipped. Faster configurations
        # (e.g. 100 B / 10 ms ≈ 10 KB/s) sometimes drop the last printed line
        # because real-world print rate dips with content density. Tune via
        # PHOMEMO_CHUNK_SIZE / PHOMEMO_CHUNK_DELAY_MS if you want to push it.
        mtu = client.mtu_size or 23
        chunk_size = int(os.environ.get("PHOMEMO_CHUNK_SIZE", str(mtu - 3)))
        chunk_delay_s = float(os.environ.get("PHOMEMO_CHUNK_DELAY_MS", "40")) / 1000.0
        total_chunks = (len(data) + chunk_size - 1) // chunk_size

        if debug:
            print(
                f"[print] writing {len(data)}B to {target_char.uuid} "
                f"(properties={target_char.properties}) — "
                f"{total_chunks} × {chunk_size}B chunks, "
                f"{chunk_delay_s * 1000:.0f}ms inter-chunk delay",
                file=sys.stderr,
            )

        # writeWithoutResponse — the M02 Pro's firmware only triggers a print
        # on this op type; write-with-response gets ack'd at LL level but the
        # buffer is ignored.
        t0 = time.time()
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            await client.write_gatt_char(target_char, chunk, response=False)
            if chunk_delay_s > 0:
                await asyncio.sleep(chunk_delay_s)
        if debug:
            print(
                f"[print] write_gatt_char stream finished in {time.time() - t0:.2f}s",
                file=sys.stderr,
            )

        # Wait for the printer to physically print before tearing down the
        # connection. M02 Pro at 300 DPI prints ~177 lines/sec; pad to 120.
        approx_lines = max(1, (len(data) - 30) // BYTES_PER_LINE)
        wait_s = max(2, approx_lines / 120 + 1)
        if debug:
            print(f"[print] sleeping {wait_s:.1f}s for print to finish", file=sys.stderr)
        await asyncio.sleep(wait_s)


def print_via_ble(data: bytes, device_name: str = "M02 Pro", debug: bool = False) -> None:
    asyncio.run(_print_async(data, device_name, debug))


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
    # --save-png overrides; --dry-run also implicitly uses this path.
    if not args.save_png:
        args.save_png = "/tmp/print-day.png"

    print("Curating today's brief with Claude…")
    brief = curate()
    print(f"  {brief.date}: {len(brief.top_picks)} top pick(s), {len(brief.remaining_tasks)} more.")

    print("Rendering to PNG…")
    image = render(brief)
    print(f"  {image.size[0]}x{image.size[1]} px")

    if args.save_png:
        image.save(args.save_png)
        print(f"Saved PNG to {args.save_png}")

    if args.dry_run:
        print("Dry run — skipping print.")
        return 0

    print("Encoding ESC/POS…")
    data = encode_escpos(image)
    print(f"  {len(data)}B")

    print("Connecting to printer…")
    debug = args.debug or bool(os.environ.get("PRINT_DEBUG"))
    print_via_ble(data, device_name=args.device, debug=debug)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
