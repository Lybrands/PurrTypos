"""Regression tests for the model-visible Agent tool contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from services.tool_contract import inspect_tool_contract


BACKEND_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BACKEND_DIR / "skills"


async def _valid_handler(ctx, args, send_chunk):  # pragma: no cover - shape fixture
    return None


def test_contract_reports_each_kind_of_drift():
    async def _wrong_signature(ctx, args):  # pragma: no cover - shape fixture
        return None

    report = inspect_tool_contract(
        [{"name": "declared"}, {"name": "declared"}, {"name": "missing"}],
        {"declared": _valid_handler, "runtimeOnly": _wrong_signature},
        {"predictorOnly": lambda *_args: False},
        {"cacheKeyOnly": lambda *_args: None},
    )

    assert report.missing_handlers == {"missing"}
    assert report.undeclared_handlers == {"runtimeOnly"}
    assert report.invalid_handlers == ("runtimeOnly",)
    assert report.orphaned_cache_predictors == {"predictorOnly"}
    assert report.orphaned_cache_keys == {"cacheKeyOnly"}
    assert report.duplicate_skill_names == {"declared"}
    assert not report.is_valid


def test_production_contract_is_valid_in_a_fresh_interpreter():
    """Avoid false confidence from handlers imported by an earlier test module."""
    script = (
        "from pathlib import Path\n"
        "from services.tool_router import set_skills_path\n"
        "set_skills_path(str(Path('skills').resolve()))\n"
        "from services.tool_executor import validate_loaded_tool_contract\n"
        "report = validate_loaded_tool_contract()\n"
        "assert report.tool_count > 0\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_skill_directory_is_reported(caplog, tmp_path):
    from services.tool_router import get_api_skill_items, set_skills_path

    (tmp_path / "incompleteSkill").mkdir()
    set_skills_path(str(tmp_path))
    try:
        assert get_api_skill_items() == []
        assert "缺少 SKILL.md" in caplog.text
    finally:
        set_skills_path(str(SKILLS_DIR))
