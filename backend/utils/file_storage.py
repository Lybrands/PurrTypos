"""存储附件文件的文件系统辅助函数。"""

from __future__ import annotations

from pathlib import Path


def safe_unlink_stored_file(stored_path: str | None, data_dir: Path | None) -> None:
    """删除 ``data_dir`` 下的相对存储文件，带路径逃逸保护。

    ``stored_path`` 是相对 ``data_dir`` 的路径。解析后若落在 ``data_dir`` 之外
    一律忽略（防目录穿越）；文件不存在或删除失败也静默跳过 —— 删附件不应因
    底层文件缺失而让整个请求失败。
    """
    if not stored_path:
        return
    base = data_dir if data_dir and data_dir != Path("") else Path(".")
    root = base.resolve()
    target = (root / stored_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        pass
