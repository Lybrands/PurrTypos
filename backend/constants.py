"""
Shared constants — extracted from duplicated definitions across
toolExecutor.js, mem0Service.js, database.js, etc.
"""

SPARK_IDEA_LAYERS: list[str] = ["全局", "大纲", "人物", "章节"]
SPARK_IDEA_LAYER_ORDERED: list[str] = ["全局", "大纲", "人物", "章节"]

FORESHADOWING_TYPES: list[str] = ["悬念", "道具", "线索", "对话"]

# Provider content uses this explicit prefix to distinguish public progress
# from an ordinary direct answer before the future tool call is known.
AGENT_PUBLIC_PROGRESS_PREFIX = "【进展】"
AGENT_PUBLIC_COMMENTARY_OPEN = "【公开说明】"
AGENT_PUBLIC_COMMENTARY_CLOSE = "【说明结束】"

BOOK_COLORS: list[str] = [
    "#4A90D9", "#E67E22", "#27AE60", "#8E44AD", "#C0392B",
    "#16A085", "#D35400", "#2C3E50", "#1ABC9C", "#E74C3C",
]
