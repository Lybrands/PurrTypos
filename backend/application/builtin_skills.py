"""Seed repo-bundled agent skills into the library as origin=builtin.

内置技能随应用发布：包文件在仓库内，启动时幂等发布到技能库（kind=skill）。
技能与写作技法是两个概念——技能不参与按书授权、手动/自动模式与检索选择；
是否允许 Agent 自动使用由包内 SKILL.md 的 metadata.autoUse 声明（默认否），
进入运行冻结快照的技能才对 Agent 可见（见 agents/writing/context_snapshot.py）。
内置技能不能被用户编辑、发布、归档或删除（见 WritingTechniqueService 守卫）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from domains.writing.techniques import TechniqueError
from infrastructure.persistence.writing.technique_document_parser import file_manifest

logger = logging.getLogger(__name__)

BUILTIN_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "agents" / "builtin_skills"

BUILTIN_SKILLS = (
    {"id": "builtin-obsidian-materials", "package": "obsidian-materials"},
    # 分析 Agent 创建写作 Skill 的子 Agent 指令包；未声明 autoUse，
    # 仅在技能库展示，注入仍走 creator_skill_resource（仓库目录是唯一源）。
    {"id": "builtin-writing-skill-creator", "package": "purrtypos-writing-skill-creator"},
)


def _package_files(package: str) -> dict[str, str]:
    root = BUILTIN_PACKAGE_ROOT / package
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    if "SKILL.md" not in files:
        raise RuntimeError(f"内置技能包缺少 SKILL.md：{package}")
    return files


async def ensure_builtin_skills(db) -> list[str]:
    """Publish bundled skill packages; idempotent per package content.

    确定性 operation id 使中断后的再次启动按收据重放整个流水线；
    包内容未变化（versionId 相同）时直接跳过。
    """
    from application.writing_technique_exchange import import_skill
    from application.writing_technique_service import WritingTechniqueService

    service = WritingTechniqueService(db)
    published = []
    for spec in BUILTIN_SKILLS:
        try:
            files = _package_files(spec["package"])
            manifest = file_manifest(files, limits=service.store("skill").limits)
            # operation id 绑定包内容：中断后同内容按收据重放，升级内容用新 id。
            operation = f"builtin:{spec['id']}:{manifest['versionId'][:16]}"
            try:
                record = await service.get_object("skill", spec["id"])
            except TechniqueError:
                record = None
            if record is not None and record.get("origin") != "builtin":
                logger.error("内置技能 ID 被占用，跳过：%s", spec["id"])
                continue
            if record is not None and record.get("publishedHead") == manifest["versionId"]:
                continue
            draft = await import_skill(service, files=files, operation_id=operation,
                skill_id=spec["id"], origin="builtin")
            sealed = await service.seal("skill", spec["id"], draft["draftId"],
                expected_revision=draft["draftRevision"], expected_tree_digest=draft["treeDigest"],
                operation_id=operation + ":seal", internal=True)
            await service.publish("skill", spec["id"], ref=sealed["sealedRef"],
                expected_published_head=record.get("publishedHead") if record else None,
                operation_id=operation + ":publish", internal=True)
            published.append(spec["id"])
        except Exception:
            logger.exception("内置技能发布失败：%s", spec["id"])
    return published
