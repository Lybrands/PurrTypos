"""Schema and immutable built-ins for the writing method library."""

from __future__ import annotations

import hashlib
import json


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(markdown: str, metadata: dict) -> str:
    payload = _json({"markdown": markdown, "metadata": metadata})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_BUILTIN_METHODS = (
    {
        "id": "builtin-method-clear-narrative",
        "revision_id": "builtin-method-clear-narrative-v1",
        "name": "清晰叙事",
        "description": "保持视角稳定、叙事清楚，并让场景目标可感知。",
        "method_type": "primary",
        "tags": ["通用", "叙事"],
        "markdown": "# 清晰叙事\n\n保持当前视角稳定。优先使用具体动作与可观察细节推进场景，避免无依据地替角色解释情绪。",
    },
    {
        "id": "builtin-method-scene-tension",
        "revision_id": "builtin-method-scene-tension-v1",
        "name": "场景张力",
        "description": "通过目标、阻力和变化组织局部场景。",
        "method_type": "technique",
        "tags": ["场景", "节奏"],
        "markdown": "# 场景张力\n\n明确场景中的即时目标与阻力。让场景结束时至少有一项关系、信息或行动条件发生变化。",
    },
)

_BUILTIN_SCHEME = {
    "id": "builtin-scheme-balanced-narrative",
    "revision_id": "builtin-scheme-balanced-narrative-v1",
    "name": "平衡叙事方案",
    "description": "清晰主叙事与场景张力的基础组合。",
    "member_revision_ids": [item["revision_id"] for item in _BUILTIN_METHODS],
}


async def init_writing_method_schema(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_methods (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        method_type TEXT NOT NULL CHECK(method_type IN ('primary', 'technique')),
        tags_json TEXT NOT NULL DEFAULT '[]',
        source_type TEXT NOT NULL DEFAULT 'user',
        source_ref_json TEXT NOT NULL DEFAULT '{}',
        is_builtin INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'disabled')),
        draft_markdown TEXT NOT NULL DEFAULT '',
        draft_metadata_json TEXT NOT NULL DEFAULT '{}',
        draft_revision INTEGER NOT NULL DEFAULT 0,
        current_published_revision_id TEXT DEFAULT NULL,
        create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_method_revisions (
        id TEXT PRIMARY KEY NOT NULL,
        method_id TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        method_type TEXT NOT NULL CHECK(method_type IN ('primary', 'technique')),
        tags_json TEXT NOT NULL DEFAULT '[]',
        markdown_body TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        content_digest TEXT NOT NULL,
        published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(method_id, version_no)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_writing_method_revisions_method "
        "ON writing_method_revisions(method_id, version_no DESC)"
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_schemes (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        draft_members_json TEXT NOT NULL DEFAULT '[]',
        draft_revision INTEGER NOT NULL DEFAULT 0,
        source_type TEXT NOT NULL DEFAULT 'user',
        source_ref_json TEXT NOT NULL DEFAULT '{}',
        is_builtin INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'disabled')),
        current_published_revision_id TEXT DEFAULT NULL,
        create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_scheme_revisions (
        id TEXT PRIMARY KEY NOT NULL,
        scheme_id TEXT NOT NULL,
        version_no INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        members_digest TEXT NOT NULL,
        published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(scheme_id, version_no)
    )""")
    await db.execute("""CREATE TABLE IF NOT EXISTS writing_scheme_revision_members (
        scheme_revision_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        method_revision_id TEXT NOT NULL,
        PRIMARY KEY(scheme_revision_id, ordinal),
        UNIQUE(scheme_revision_id, method_revision_id)
    )""")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_writing_scheme_members_method "
        "ON writing_scheme_revision_members(method_revision_id)"
    )
    await db.execute("""CREATE TABLE IF NOT EXISTS book_writing_method_bindings (
        id TEXT PRIMARY KEY NOT NULL,
        book_id TEXT NOT NULL,
        binding_type TEXT NOT NULL CHECK(binding_type IN ('method', 'scheme')),
        method_revision_id TEXT DEFAULT NULL,
        scheme_revision_id TEXT DEFAULT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'user',
        create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(
            (binding_type = 'method' AND method_revision_id IS NOT NULL AND scheme_revision_id IS NULL)
            OR
            (binding_type = 'scheme' AND scheme_revision_id IS NOT NULL AND method_revision_id IS NULL)
        )
    )""")
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_book_method_binding_unique "
        "ON book_writing_method_bindings(book_id, method_revision_id) "
        "WHERE method_revision_id IS NOT NULL"
    )
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_book_scheme_binding_unique "
        "ON book_writing_method_bindings(book_id, scheme_revision_id) "
        "WHERE scheme_revision_id IS NOT NULL"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_book_writing_method_bindings_order "
        "ON book_writing_method_bindings(book_id, priority, create_time)"
    )
    await _seed_builtins(db)


async def _seed_builtins(db) -> None:
    async with db.transaction():
        for item in _BUILTIN_METHODS:
            metadata = {"schemaVersion": 1, "builtin": True}
            await db.execute(
                "INSERT OR IGNORE INTO writing_methods "
                "(id, name, description, method_type, tags_json, source_type, "
                "is_builtin, status, draft_markdown, draft_metadata_json, "
                "draft_revision, current_published_revision_id) "
                "VALUES (?, ?, ?, ?, ?, 'builtin', 1, 'active', ?, ?, 0, ?)",
                [
                    item["id"], item["name"], item["description"],
                    item["method_type"], _json(item["tags"]), item["markdown"],
                    _json(metadata), item["revision_id"],
                ],
            )
            await db.execute(
                "INSERT OR IGNORE INTO writing_method_revisions "
                "(id, method_id, version_no, name, description, method_type, "
                "tags_json, markdown_body, metadata_json, content_digest) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item["revision_id"], item["id"], item["name"],
                    item["description"], item["method_type"], _json(item["tags"]),
                    item["markdown"], _json(metadata),
                    _digest(item["markdown"], metadata),
                ],
            )
        scheme = _BUILTIN_SCHEME
        await db.execute(
            "INSERT OR IGNORE INTO writing_schemes "
            "(id, name, description, draft_members_json, draft_revision, "
            "source_type, is_builtin, status, current_published_revision_id) "
            "VALUES (?, ?, ?, ?, 0, 'builtin', 1, 'active', ?)",
            [
                scheme["id"], scheme["name"], scheme["description"],
                _json(scheme["member_revision_ids"]), scheme["revision_id"],
            ],
        )
        await db.execute(
            "INSERT OR IGNORE INTO writing_scheme_revisions "
            "(id, scheme_id, version_no, name, description, metadata_json, members_digest) "
            "VALUES (?, ?, 1, ?, ?, ?, ?)",
            [
                scheme["revision_id"], scheme["id"], scheme["name"],
                scheme["description"], _json({"schemaVersion": 1, "builtin": True}),
                hashlib.sha256(_json(scheme["member_revision_ids"]).encode("utf-8")).hexdigest(),
            ],
        )
        for ordinal, revision_id in enumerate(scheme["member_revision_ids"]):
            await db.execute(
                "INSERT OR IGNORE INTO writing_scheme_revision_members "
                "(scheme_revision_id, ordinal, method_revision_id) VALUES (?, ?, ?)",
                [scheme["revision_id"], ordinal, revision_id],
            )


__all__ = ["init_writing_method_schema"]
