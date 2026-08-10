"""
backend/ 不是 src layout 包，运行时入口（main.py）依赖 cwd=backend 才能 `from constants import ...`。
跑 pytest 时 rootdir 也是 backend，但保险起见把 backend 自身塞到 sys.path 头部，避免任何
启动方式差异（IDE / CI / Windows / Unix）导致 ImportError。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
PURRA_SRC_DIR = ROOT_DIR / "packages" / "purra" / "src"

for source_dir in (BACKEND_DIR, PURRA_SRC_DIR):
    source = str(source_dir)
    if source not in sys.path:
        sys.path.insert(0, source)
