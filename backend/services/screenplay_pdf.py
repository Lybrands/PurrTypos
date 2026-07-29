"""Render an accepted Fountain-compatible screenplay as a polished PDF."""

from __future__ import annotations

import io
import os
import re
from html import escape
from pathlib import Path


_SCENE_HEADING = re.compile(
    r"^(?:INT\.?|EXT\.?|INT\./EXT\.?|EXT\./INT\.?|内景|外景|内外景)"
    r"(?:\s|[.·\-—])",
    re.IGNORECASE,
)
_TRANSITION = re.compile(
    r"(?:TO:|切至[:：]|淡入[:：]|淡出[:：])$",
    re.IGNORECASE,
)
_ASCII_CHARACTER = re.compile(r"^[A-Z0-9 ._()'\-]{1,40}$")
_CJK_CHARACTER = re.compile(r"^[\u3400-\u9fffA-Za-z0-9· ]{1,16}[：:]?$")
_HAS_CJK = re.compile(r"[\u3400-\u9fff]")


def build_screenplay_pdf(
    *,
    title: str,
    screenplay_format: str,
    content: str,
) -> bytes:
    """Build a US-Letter screenplay PDF with a title page and page numbers."""

    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    normalized_title = str(title or "").strip() or "未命名剧本"
    normalized_format = str(screenplay_format or "").strip() or "剧本"
    normalized_content = str(content or "").replace("\r\n", "\n").replace(
        "\r",
        "\n",
    )
    has_cjk = bool(_HAS_CJK.search(
        f"{normalized_title}{normalized_format}{normalized_content}"
    ))
    if has_cjk:
        font_path = _find_cjk_font()
        if font_path is None:
            raise RuntimeError(
                "缺少可嵌入的中文字体，无法生成可靠的剧本 PDF"
            )
        body_font = "PurrTyposCJK"
        if body_font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(body_font, str(font_path)))
    else:
        body_font = "Courier"

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=1.5 * inch,
        rightMargin=1.0 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.8 * inch,
        title=normalized_title,
        author="PurrTypos",
        subject=f"{normalized_format} screenplay",
    )
    common = {
        "fontName": body_font,
        "fontSize": 12,
        "leading": 16 if has_cjk else 14,
        "textColor": "#111111",
        "wordWrap": "CJK" if has_cjk else None,
        "allowWidows": 0,
        "allowOrphans": 0,
    }
    styles = {
        "title": ParagraphStyle(
            "ScreenplayTitle",
            fontName=body_font,
            fontSize=26,
            leading=34,
            alignment=TA_CENTER,
            spaceAfter=20,
            wordWrap="CJK" if has_cjk else None,
        ),
        "subtitle": ParagraphStyle(
            "ScreenplaySubtitle",
            fontName=body_font,
            fontSize=12,
            leading=18,
            alignment=TA_CENTER,
            textColor="#555555",
        ),
        "action": ParagraphStyle(
            "ScreenplayAction",
            alignment=TA_LEFT,
            spaceAfter=8,
            **common,
        ),
        "scene": ParagraphStyle(
            "ScreenplayScene",
            alignment=TA_LEFT,
            spaceBefore=10,
            spaceAfter=6,
            keepWithNext=True,
            **common,
        ),
        "character": ParagraphStyle(
            "ScreenplayCharacter",
            alignment=TA_LEFT,
            leftIndent=2.2 * inch,
            rightIndent=0.8 * inch,
            spaceBefore=8,
            spaceAfter=1,
            **common,
        ),
        "dialogue": ParagraphStyle(
            "ScreenplayDialogue",
            alignment=TA_LEFT,
            leftIndent=1.2 * inch,
            rightIndent=0.75 * inch,
            spaceAfter=7,
            **common,
        ),
        "parenthetical": ParagraphStyle(
            "ScreenplayParenthetical",
            alignment=TA_LEFT,
            leftIndent=1.65 * inch,
            rightIndent=1.0 * inch,
            spaceAfter=1,
            **common,
        ),
        "transition": ParagraphStyle(
            "ScreenplayTransition",
            alignment=TA_RIGHT,
            spaceBefore=8,
            spaceAfter=8,
            **common,
        ),
    }

    story = [
        Spacer(1, 2.35 * inch),
        Paragraph(escape(normalized_title), styles["title"]),
        Paragraph(
            escape(f"{normalized_format} · 剧本"),
            styles["subtitle"],
        ),
        Spacer(1, 2.2 * inch),
        Paragraph("PURR TYPOS", styles["subtitle"]),
        PageBreak(),
    ]
    lines = normalized_content.split("\n")
    dialogue_mode = False
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            dialogue_mode = False
            story.append(Spacer(1, 4))
            continue
        if line == "===":
            dialogue_mode = False
            story.append(PageBreak())
            continue
        next_line = _next_nonempty_line(lines, index + 1)
        if _SCENE_HEADING.match(line):
            dialogue_mode = False
            story.append(Paragraph(escape(line), styles["scene"]))
            continue
        if _TRANSITION.search(line):
            dialogue_mode = False
            story.append(Paragraph(escape(line), styles["transition"]))
            continue
        if line.startswith("@") or _is_character_cue(line, next_line):
            dialogue_mode = True
            cue = line[1:].strip() if line.startswith("@") else line
            cue = cue.rstrip("：:")
            story.append(Paragraph(escape(cue), styles["character"]))
            continue
        if dialogue_mode and _is_parenthetical(line):
            story.append(Paragraph(escape(line), styles["parenthetical"]))
            continue
        if dialogue_mode:
            story.append(Paragraph(escape(line), styles["dialogue"]))
            continue
        story.append(Paragraph(escape(line), styles["action"]))

    def _later_page(canvas, doc) -> None:
        body_page = canvas.getPageNumber() - 1
        if body_page < 1:
            return
        canvas.saveState()
        canvas.setFont(body_font, 10)
        canvas.setFillColor("#444444")
        canvas.drawRightString(
            LETTER[0] - 0.75 * inch,
            LETTER[1] - 0.48 * inch,
            f"{body_page}.",
        )
        canvas.restoreState()

    document.build(
        story,
        onFirstPage=lambda canvas, doc: None,
        onLaterPages=_later_page,
    )
    return buffer.getvalue()


def _next_nonempty_line(lines: list[str], start: int) -> str:
    for raw_line in lines[start:]:
        line = raw_line.strip()
        if line:
            return line
    return ""


def _is_character_cue(line: str, next_line: str) -> bool:
    if _ASCII_CHARACTER.fullmatch(line) and not _SCENE_HEADING.match(line):
        return True
    return (
        bool(_CJK_CHARACTER.fullmatch(line))
        and _is_parenthetical(next_line)
    )


def _is_parenthetical(line: str) -> bool:
    value = str(line or "").strip()
    return (
        (value.startswith("(") and value.endswith(")"))
        or (value.startswith("（") and value.endswith("）"))
    )


def _find_cjk_font() -> Path | None:
    windows_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    candidates = (
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        windows_root / "Fonts" / "msyh.ttc",
        windows_root / "Fonts" / "msyh.ttf",
        windows_root / "Fonts" / "simsun.ttc",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )
    return next((path for path in candidates if path.is_file()), None)
