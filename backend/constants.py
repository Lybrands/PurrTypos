"""
Shared constants — extracted from duplicated definitions across
toolExecutor.js, mem0Service.js, database.js, etc.
"""

MEMORY_LAYERS: list[str] = ["全局", "大纲", "人物", "章节"]
MEMORY_LAYER_ORDERED: list[str] = ["全局", "大纲", "人物", "章节"]

FORESHADOWING_TYPES: list[str] = ["悬念", "道具", "线索", "对话"]

BOOK_COLORS: list[str] = [
    "#4A90D9", "#E67E22", "#27AE60", "#8E44AD", "#C0392B",
    "#16A085", "#D35400", "#2C3E50", "#1ABC9C", "#E74C3C",
]

OUTLINE_TYPE_LABEL: dict[str, str] = {
    "global": "总纲",
    "volume": "卷大纲",
    "chapter": "章节大纲",
    "other": "其他大纲",
    "writing": "写作大纲",
}
