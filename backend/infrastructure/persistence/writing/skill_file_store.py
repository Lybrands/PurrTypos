"""Agent skill packages: SKILL.md file collections beside techniques and schemes."""

from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore


class SkillFileStore(TechniqueFileStore):
    """第三集合：与技法同构的 SKILL.md 文件包，独立目录与 kind 命名空间。"""

    collection = "skills"
    record_kind = "skill"


__all__ = ["SkillFileStore"]
