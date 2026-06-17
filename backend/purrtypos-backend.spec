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
    "database.connection",
    "database.schema",
    "database.crud.outlines",
    "database.crud.chapters",
    "database.crud.articles",
    "database.crud.characters",
    "database.crud.sessions",
    "database.crud.conversations",
    "database.crud.ai_favorites",
    "database.crud.prompt_templates",
    "database.crud.settings",
    "database.crud.story_background",
    "services.memory_service",
    "services.tool_router",
    "services.tool_executor",
    "services.tool_runtime",
    "services.tool_registry",
    "services.tool_context",
    "services.tool_chapter_access",
    "services.tool_data_loaders",
    "services.tool_read_cache_keys",
    "services.tool_handlers.chapter_tools",
    "services.tool_handlers.context_tools",
    "services.tool_handlers.memory_tools",
    "services.tool_handlers.outline_tools",
    "services.agent_tool_definitions",
    "services.writing_rules",
    "utils.tooling_context",
    "utils.chat_preflight",
    "utils.writing_helpers",
    "services.openai_chat",
    "services.anthropic_chat",
    "services.ai_provider",
    "services.ai_capabilities",
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
