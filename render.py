"""Render a curated daily Brief to a Pillow image.

Output width matches phomemo.PRINT_WIDTH (the M02 Pro's 560 dots). The
Brief data classes are the public input contract; build one by hand if
you want to drive the rendering directly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from phomemo import PRINT_WIDTH

REPO_ROOT = Path(__file__).resolve().parent
FONTS_DIR = REPO_ROOT / "fonts"

Priority = Literal["high", "medium", "low", "none"]


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


PADDING_X = 20
PADDING_Y = 24
PRIORITY_DOT = 8
PRIORITY_DOT_GAP = 3
PRIORITY_WIDTH = 3 * PRIORITY_DOT + 2 * PRIORITY_DOT_GAP
NUMBER_COL_WIDTH = 36
CHECKBOX_SIZE = 22
CANVAS_HEIGHT = 6000
BW_THRESHOLD = 170  # luminance < this becomes ink when collapsing to 1-bit


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate_to_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> str:
    if _text_w(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    for i in range(len(text), 0, -1):
        candidate = text[:i] + ellipsis
        if _text_w(draw, candidate, font) <= max_width:
            return candidate
    return ellipsis


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = [_truncate_to_width(draw, w, font, max_width) for w in text.split()]
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
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        # Bad/unrecognised date — keep rendering, just skip the day name.
        print(f"  warning: could not parse date {iso_date!r}; omitting day name", file=sys.stderr)
        return ""


def render(brief: Brief) -> Image.Image:
    canvas = Image.new("L", (PRINT_WIDTH, CANVAS_HEIGHT), color=255)
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

    if y > CANVAS_HEIGHT:
        raise RuntimeError(
            f"rendered content height {y}px exceeds canvas height {CANVAS_HEIGHT}px — "
            f"raise CANVAS_HEIGHT or trim the brief"
        )

    # Crop to content height
    canvas = canvas.crop((0, 0, PRINT_WIDTH, y))
    # Threshold to 1-bit (0 = black, 255 = white)
    canvas = canvas.point(lambda p: 0 if p < BW_THRESHOLD else 255, mode="1")
    return canvas
