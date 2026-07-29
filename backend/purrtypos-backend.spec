# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — Windows onedir bundle for PurrTypos Python backend.

From repo root:
  py -m PyInstaller --clean --noconfirm backend/purrtypos-backend.spec

From backend/:
  py -m PyInstaller --clean --noconfirm purrtypos-backend.spec
"""
from __future__ import annotations

import os
import sys

_spec_dir = os.path.dirname(os.path.abspath(SPEC))
if _spec_dir not in sys.path:
    sys.path.insert(0, _spec_dir)

_skills_src = os.path.join(_spec_dir, "skills")
_datas = []
if os.path.isdir(_skills_src):
    _datas.append((_skills_src, "skills"))

_hiddenimports = [
    "certifi",
    "aiosqlite",
    "yaml",
    "httpx",
    "openai",
    "anthropic",
    "sse_starlette.sse",
    "pydantic",
    "pydantic.deprecated.decorator",
    "reportlab",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfgen.canvas",
    "reportlab.platypus",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.__main__",
    "routers.books",
    "routers.outlines",
    "routers.chapters",
    "routers.articles",
    "routers.characters",
    "routers.sessions",
    "routers.conversations",
    "routers.memories",
    "routers.ai",
    "routers.settings",
    "routers.story_background",
    "routers.files",
    "routers.prompt_templates",
    "routers.screenplay",
    "database.connection",
    "database.schema",
    "database.crud.outlines",
    "database.crud.chapters",
    "database.crud.articles",
    "database.crud.characters",
    "database.crud.sessions",
    "database.crud.ai_favorites",
    "database.crud.prompt_templates",
    "database.crud.settings",
    "database.crud.story_background",
    "services.memory_service",
    "services.screenplay_pdf",
    "infrastructure.models.openai_chat",
    "infrastructure.models.anthropic_chat",
    "infrastructure.models.provider_router",
    "infrastructure.models.capabilities",
]

a = Analysis(
    [os.path.join(_spec_dir, "main.py")],
    pathex=[_spec_dir],
    binaries=[],
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="purrtypos-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="purrtypos-backend",
)
