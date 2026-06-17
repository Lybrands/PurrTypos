"""
书籍导出：EPUB 生成（纯标准库 zipfile，无新依赖）。

按章分文件的 MD/TXT 导出在前端组装（已有能力）；
EPUB 是二进制 zip，由后端直接产出字节流，Electron 主进程负责保存对话框与写盘。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Response

from dependencies import get_db
from schemas.export import ExportEpubRequest
from utils.book_structure import get_ordered_leaf_chapters, load_chapter_texts

router = APIRouter(tags=["export"])

_EPUB_CSS = """\
body { line-height: 1.8; margin: 1em; }
h1 { font-size: 1.3em; text-align: center; margin: 1.5em 0 1em; }
h2.volume { font-size: 1.2em; text-align: center; margin: 1.2em 0 0.6em; color: #555; }
p { text-indent: 2em; margin: 0.6em 0; }
"""


def _chapter_xhtml(title: str, volume_title: str | None, text: str) -> str:
    paras = [p.strip() for p in (text or "").replace("\r\n", "\n").split("\n") if p.strip()]
    body_parts = []
    if volume_title:
        body_parts.append(f'<h2 class="volume">{escape(volume_title)}</h2>')
    body_parts.append(f"<h1>{escape(title)}</h1>")
    if paras:
        body_parts.extend(f"<p>{escape(p)}</p>" for p in paras)
    else:
        body_parts.append("<p>（本章暂无内容）</p>")
    body = "\n".join(body_parts)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh">\n'
        f"<head><title>{escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def build_epub(title: str, chapters: list[dict]) -> bytes:
    """chapters: [{title, volume_title|None, text}]，已按阅读顺序排列。"""
    book_uuid = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_items: list[str] = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items: list[str] = []
    nav_lis: list[str] = []
    ncx_points: list[str] = []
    files: list[tuple[str, str]] = []

    last_volume: str | None = None
    for i, ch in enumerate(chapters, start=1):
        fname = f"chapter{i:04d}.xhtml"
        # 卷标题只在卷的第一章页面展示
        vol = ch.get("volume_title")
        show_volume = vol if vol and vol != last_volume else None
        last_volume = vol
        files.append((
            f"OEBPS/{fname}",
            _chapter_xhtml(ch["title"], show_volume, ch.get("text") or ""),
        ))
        manifest_items.append(
            f'<item id="ch{i}" href="{fname}" media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="ch{i}"/>')
        nav_label = escape(f"{vol} · {ch['title']}" if vol else ch["title"])
        nav_lis.append(f'<li><a href="{fname}">{nav_label}</a></li>')
        ncx_points.append(
            f'<navPoint id="np{i}" playOrder="{i}">'
            f"<navLabel><text>{nav_label}</text></navLabel>"
            f'<content src="{fname}"/></navPoint>'
        )

    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"<dc:identifier id=\"bookid\">{book_uuid}</dc:identifier>\n"
        f"<dc:title>{escape(title)}</dc:title>\n"
        "<dc:language>zh</dc:language>\n"
        f'<meta property="dcterms:modified">{modified}</meta>\n'
        "</metadata>\n"
        f"<manifest>\n{chr(10).join(manifest_items)}\n</manifest>\n"
        f'<spine toc="ncx">\n{chr(10).join(spine_items)}\n</spine>\n'
        "</package>\n"
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
        f"<head><title>{escape(title)}</title></head>\n"
        '<body><nav epub:type="toc"><h1>目录</h1>\n'
        f"<ol>\n{chr(10).join(nav_lis)}\n</ol>\n"
        "</nav></body></html>\n"
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_uuid}"/></head>\n'
        f"<docTitle><text>{escape(title)}</text></docTitle>\n"
        f"<navMap>\n{chr(10).join(ncx_points)}\n</navMap>\n"
        "</ncx>\n"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n'
        "</container>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # EPUB 规范：mimetype 必须是首个条目且不压缩
        zf.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", _EPUB_CSS, zipfile.ZIP_DEFLATED)
        for path, content in files:
            zf.writestr(path, content, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


@router.post("/export/epub")
async def export_epub(body: ExportEpubRequest):
    db = get_db()
    book = await db.fetch_one("SELECT * FROM books WHERE id = ?", [body.bookId])
    if not book:
        return {"success": False, "error": "书籍不存在"}

    leaves = await get_ordered_leaf_chapters(db, str(body.bookId))
    if body.chapterIds:
        wanted = {str(c) for c in body.chapterIds}
        leaves = [c for c in leaves if c["id"] in wanted]
    if not leaves:
        return {"success": False, "error": "没有可导出的章节"}

    texts = await load_chapter_texts(db, [c["id"] for c in leaves])
    chapters = [{
        "title": c["title"] or f"第 {c['index']} 章",
        "volume_title": c["volume_title"],
        "text": texts.get(c["id"], ""),
    } for c in leaves]

    data = build_epub(str(book.get("title") or "未命名"), chapters)
    return Response(content=data, media_type="application/epub+zip")
