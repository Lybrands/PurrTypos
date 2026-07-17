from __future__ import annotations

import logging
from pathlib import Path

from infrastructure.writing.skill_catalog import WritingSkillCatalog


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A test tool",
    schema: str = '{"type":"object","properties":{}}',
) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        "# Tool\n\n"
        f"```json\n{schema}\n```\n",
        encoding="utf-8",
    )


def test_catalog_instances_are_isolated_by_skills_directory(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _write_skill(first_dir, "alpha")
    _write_skill(second_dir, "beta")

    first = WritingSkillCatalog(first_dir)
    second = WritingSkillCatalog(second_dir)

    assert [item["name"] for item in first.load()] == ["alpha"]
    assert [item["name"] for item in second.load()] == ["beta"]
    assert [item["name"] for item in first.skill_items()] == ["alpha"]


def test_repeated_loads_are_stable_and_results_are_defensive_copies(tmp_path):
    _write_skill(tmp_path, "zeta")
    _write_skill(
        tmp_path,
        "alpha",
        schema='{"properties":{"value":{"type":"string"}}}',
    )
    catalog = WritingSkillCatalog(tmp_path)

    first = catalog.load()
    second = catalog.load()

    assert first == second
    assert [item["name"] for item in second] == ["alpha", "zeta"]
    assert second[0]["parameters"]["type"] == "object"

    first[0]["parameters"]["properties"]["leak"] = {"type": "boolean"}
    assert "leak" not in catalog.skill_items()[0]["parameters"]["properties"]


def test_invalid_skills_are_skipped_without_hiding_valid_siblings(
    tmp_path,
    caplog,
):
    _write_skill(tmp_path, "valid")
    _write_skill(tmp_path, "missingDescription", description="")
    _write_skill(tmp_path, "invalidSchema", schema='{"type":"string"}')
    (tmp_path / "missingMarkdown").mkdir()
    _write_skill(tmp_path, ".hidden")

    catalog = WritingSkillCatalog(tmp_path)
    with caplog.at_level(logging.WARNING):
        items = catalog.load()

    assert [item["name"] for item in items] == ["valid"]
    assert "missingDescription" in caplog.text
    assert "invalidSchema" in caplog.text
    assert "missingMarkdown" in caplog.text
    assert ".hidden" not in caplog.text
