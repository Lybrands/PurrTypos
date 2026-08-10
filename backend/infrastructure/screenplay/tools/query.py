"""Bounded, scope-checked reads for screenplay tools."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from domains.screenplay.source_scope import (
    is_restricted_source_scope,
    scoped_chapters,
    scoped_outline_ids,
    source_scope_summary,
)
from domains.screenplay_agent.tools.errors import ScreenplayToolInputError
from infrastructure.persistence.writing import SqliteStoryMemoryRecallRepository
from utils.book_structure import load_chapter_texts
from utils.text import extract_text_from_lexical


def _json(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}\n…（已截断）"


def _page(arguments: Mapping[str, Any]) -> tuple[int, int]:
    return max(0, int(arguments.get("cursor") or 0)), max(
        1, min(50, int(arguments.get("limit") or 20))
    )


class ScreenplayToolQuery:
    def __init__(self, db) -> None:
        self._db = db
        self._screenplay = ScreenplayAgentContextQuery(db)
        self._story_memory = SqliteStoryMemoryRecallRepository(db)

    async def inspect_project(self, scope, _arguments) -> dict[str, Any]:
        project = await self._project(scope)
        heads = await self._screenplay.heads(
            str(project["id"]),
            text_limit=0,
        )
        return {
            "project": {
                "id": str(project["id"]),
                "title": str(project.get("title") or ""),
                "format": str(project.get("format") or ""),
                "stage": str(project.get("active_stage") or ""),
                "sourceKind": str(project.get("source_kind") or "original"),
                "sourceScope": _tool_scope_summary(
                    project.get("source_scope_json")
                ),
            },
            "acceptedDeliverables": [{
                "role": item["role"],
                "revisionId": item["revisionId"],
            } for item in heads],
        }

    async def read_deliverable(self, scope, arguments) -> dict[str, Any]:
        project_id = str(scope["projectId"])
        role = str(arguments["role"])
        revision_id = str(arguments.get("revisionId") or "").strip()
        if revision_id:
            row = await self._db.fetch_one(
                "SELECT r.id FROM screenplay_revisions AS r "
                "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
                [revision_id, project_id, role],
            )
            if row is None:
                raise ScreenplayToolInputError(
                    "revisionId does not identify the requested role in this project.",
                    guidance=(
                        "Use a revisionId returned for the same role by "
                        "inspectScreenplayProject or searchScreenplayDeliverables."
                    ),
                    details={"invalidRevisionId": revision_id, "role": role},
                )
        else:
            row = await self._db.fetch_one(
                "SELECT h.revision_id AS id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ? AND d.role = ?",
                [project_id, role],
            )
            if row is None:
                return {"role": role, "available": False}
            revision_id = str(row["id"])
        episode = arguments.get("episodeNumber")
        if episode is not None:
            part = await self._db.fetch_one(
                "SELECT payload_json, content_text FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'episode' AND part_key = ?",
                [revision_id, str(int(episode))],
            )
        else:
            part = await self._db.fetch_one(
                "SELECT payload_json, content_text FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'document' "
                "AND part_key = 'main'",
                [revision_id],
            )
        return {
            "role": role,
            "revisionId": revision_id,
            "available": part is not None,
            **(
                {
                    "payload": _bounded_payload(
                        _json(part.get("payload_json")),
                        32_000,
                    ),
                    "contentText": _clip(part.get("content_text"), 32_000),
                }
                if part is not None
                else {}
            ),
        }

    async def search_deliverables(self, scope, arguments) -> dict[str, Any]:
        query = str(arguments["query"]).strip()
        roles = tuple(str(item) for item in arguments.get("roles") or ())
        limit = max(1, min(50, int(arguments.get("limit") or 12)))
        params: list[object] = [str(scope["projectId"]), f"%{query}%"]
        role_sql = ""
        if roles:
            role_sql = " AND d.role IN (" + ",".join("?" for _ in roles) + ")"
            params.extend(roles)
        params.append(limit)
        rows = await self._db.fetch_all(
            "SELECT h.revision_id, d.role, p.part_type, p.part_key, "
            "p.content_text FROM screenplay_revisions AS r "
            "JOIN screenplay_project_heads AS h ON h.revision_id = r.id "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revision_parts AS p ON p.revision_id = h.revision_id "
            "WHERE h.project_id = ? AND p.content_text LIKE ?"
            f"{role_sql} ORDER BY d.role, p.position LIMIT ?",
            params,
        )
        return {"matches": [{
            "revisionId": str(row["revision_id"]),
            "role": str(row["role"]),
            "partType": str(row["part_type"]),
            "partKey": str(row["part_key"]),
            "excerpt": _excerpt(str(row.get("content_text") or ""), query),
        } for row in rows]}

    async def episode_context(self, scope, arguments) -> dict[str, Any]:
        project_id = str(scope["projectId"])
        episode_number = int(arguments["episodeNumber"])
        revision_id = str(arguments.get("draftRevisionId") or "").strip() or None
        if revision_id and not await self._revision_matches_role(
            project_id, revision_id, "screenplayDraft"
        ):
            raise ScreenplayToolInputError(
                "draftRevisionId is not a screenplayDraft revision in this project.",
                guidance=(
                    "Use the screenplayDraft revisionId returned by "
                    "inspectScreenplayProject."
                ),
                details={"invalidDraftRevisionId": revision_id},
            )
        available = await self._screenplay.available_episode_numbers(
            project_id,
            draft_revision_id=revision_id,
        )
        if episode_number not in available["sceneList"]:
            raise ScreenplayToolInputError(
                "episodeNumber does not exist in the accepted scene list.",
                guidance=(
                    "Use an episode number present in the accepted sceneList "
                    "deliverable."
                ),
                details={
                    "invalidEpisodeNumber": episode_number,
                    "availableEpisodeNumbers": list(available["sceneList"]),
                },
            )
        context = await self._screenplay.episode_context(
            project_id,
            episode_number,
            draft_revision_id=revision_id,
        )
        return {
            **context,
            "previousEpisode": _continuity_only(context.get("previousEpisode")),
            "currentDraft": _bounded_payload(
                context.get("currentDraft"),
                60_000,
            ),
        }

    async def inspect_source_structure(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        chapters = await scoped_chapters(self._db, project)
        cursor, limit = _page(arguments)
        page = chapters[cursor:cursor + limit]
        return {
            "scope": _tool_scope_summary(project.get("source_scope_json")),
            "chapters": [{
                "chapterId": str(item["id"]),
                "title": str(item.get("title") or ""),
                "index": int(item.get("index") or 0),
                "volumeId": str(item.get("volume_id") or "") or None,
                "volumeTitle": str(item.get("volume_title") or "") or None,
            } for item in page],
            "nextCursor": cursor + len(page),
            "hasMore": cursor + len(page) < len(chapters),
        }

    async def read_source_chapters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        allowed = {
            str(item["id"]): item
            for item in await scoped_chapters(self._db, project)
        }
        requested = tuple(str(item) for item in arguments["chapterIds"])
        outside = set(requested) - set(allowed)
        if outside:
            mappings = await self._outline_chapter_mappings(
                project,
                outside,
                allowed_chapter_ids=set(allowed),
            )
            raise ScreenplayToolInputError(
                "chapterIds contains identifiers outside the readable adaptation scope.",
                guidance=(
                    "Use chapterId from inspectSourceStructure or the chapterId "
                    "field returned by readSourceOutline; do not pass outlineId."
                ),
                details={
                    "invalidChapterIds": sorted(outside),
                    **({"outlineIdMappings": mappings} if mappings else {}),
                },
            )
        texts = await load_chapter_texts(self._db, requested)
        text_limit = max(2_000, 48_000 // len(requested))
        return {"chapters": [{
            "chapterId": chapter_id,
            "title": str(allowed[chapter_id].get("title") or ""),
            "index": int(allowed[chapter_id].get("index") or 0),
            "content": _clip(texts.get(chapter_id, ""), text_limit),
        } for chapter_id in requested]}

    async def search_source_text(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        query = str(arguments["query"]).strip()
        limit = max(1, min(50, int(arguments.get("limit") or 12)))
        chapters = await scoped_chapters(self._db, project)
        allowed = {str(item["id"]): item for item in chapters}
        matches: list[dict[str, Any]] = []
        chapter_ids = list(allowed)
        for offset in range(0, len(chapter_ids), 400):
            batch = chapter_ids[offset:offset + 400]
            marks = ",".join("?" for _ in batch)
            rows = await self._db.fetch_all(
                "SELECT chapter_id, content FROM articles "
                f"WHERE chapter_id IN ({marks}) AND content LIKE ?",
                [*batch, f"%{query}%"],
            )
            rows.sort(key=lambda row: int(
                allowed[str(row["chapter_id"])].get("index") or 0
            ))
            for row in rows:
                raw = str(row.get("content") or "")
                text = extract_text_from_lexical(raw) if raw else ""
                matches.append({
                    "chapterId": str(row["chapter_id"]),
                    "chapterTitle": str(
                        allowed[str(row["chapter_id"])].get("title") or ""
                    ),
                    "excerpt": _excerpt(text, query),
                })
            if len(matches) >= limit:
                break
        return {"matches": matches[:limit]}

    async def list_characters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "人物资料")
        cursor, limit = _page(arguments)
        query = str(arguments.get("query") or "").strip()
        params: list[object] = [str(project["source_book_id"])]
        query_sql = ""
        if query:
            query_sql = " AND (name LIKE ? OR tags LIKE ?)"
            params.extend((f"%{query}%", f"%{query}%"))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, name, tags FROM characters WHERE book_id = ?"
            f"{query_sql} ORDER BY id LIMIT ? OFFSET ?",
            params,
        )
        return _catalog_page(
            rows,
            cursor,
            limit,
            "characters",
            id_key="characterId",
        )

    async def read_characters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "人物资料")
        ids = tuple(int(item) for item in arguments["characterIds"])
        rows = await self._rows_by_ids(
            "characters", str(project["source_book_id"]), ids
        )
        profile_limit = max(1_500, 40_000 // len(ids))
        return {"characters": [{
            "characterId": int(row["id"]),
            "name": str(row.get("name") or ""),
            "tags": str(row.get("tags") or ""),
            "profile": _clip(row.get("profile_md"), profile_limit),
        } for row in rows]}

    async def list_world_entities(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "世界设定")
        cursor, limit = _page(arguments)
        types = tuple(str(item) for item in arguments.get("types") or ())
        query = str(arguments.get("query") or "").strip()
        clauses = ["book_id = ?"]
        params: list[object] = [str(project["source_book_id"])]
        if types:
            clauses.append("entity_type IN (" + ",".join("?" for _ in types) + ")")
            params.extend(types)
        if query:
            clauses.append("(name LIKE ? OR tags LIKE ?)")
            params.extend((f"%{query}%", f"%{query}%"))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, entity_type, name, tags FROM setting_entities WHERE "
            + " AND ".join(clauses)
            + " ORDER BY entity_type, id LIMIT ? OFFSET ?",
            params,
        )
        return _catalog_page(
            rows,
            cursor,
            limit,
            "entities",
            id_key="entityId",
        )

    async def read_world_entities(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "世界设定")
        ids = tuple(int(item) for item in arguments["entityIds"])
        rows = await self._rows_by_ids(
            "setting_entities", str(project["source_book_id"]), ids
        )
        profile_limit = max(1_500, 40_000 // len(ids))
        return {"entities": [{
            "entityId": int(row["id"]),
            "type": str(row.get("entity_type") or "other"),
            "name": str(row.get("name") or ""),
            "tags": str(row.get("tags") or ""),
            "profile": _clip(row.get("profile_md"), profile_limit),
        } for row in rows]}

    async def read_background(self, scope, _arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "故事背景")
        row = await self._db.fetch_one(
            "SELECT content FROM story_background WHERE book_id = ?",
            [str(project["source_book_id"])],
        )
        return {"content": _clip((row or {}).get("content"), 24_000)}

    async def query_story_facts(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        chapter_ids = tuple(
            str(item["id"]) for item in await scoped_chapters(self._db, project)
        )
        items = await self._story_memory.search_current(
            str(project["source_book_id"]),
            str(arguments["query"]),
            kinds=tuple(str(item) for item in arguments.get("kinds") or ()),
            chapter_ids=chapter_ids,
            limit=max(1, min(50, int(arguments.get("limit") or 16))),
        )
        return {"facts": [{
            "factId": item.record_id,
            "kind": item.kind,
            "key": item.memory_key,
            "payload": _bounded_payload(item.payload, 3_000),
            "evidence": {
                "chapterId": item.chapter_id,
                "excerpt": _clip(item.source_excerpt, 1_200),
            },
        } for item in items]}

    async def read_outline(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        requested = tuple(str(item) for item in arguments.get("outlineIds") or ())
        allowed = await scoped_outline_ids(self._db, project)
        params: list[object] = [str(project["source_book_id"])]
        clauses = ["book_id = ?", "type != 'writing'"]
        if is_restricted_source_scope(project):
            if not allowed:
                return {
                    "outlines": [],
                    "nextCursor": int(arguments.get("cursor") or 0),
                    "hasMore": False,
                }
            if requested and set(requested) - allowed:
                outside = set(requested) - allowed
                raise ScreenplayToolInputError(
                    "outlineIds contains identifiers outside the adaptation scope.",
                    guidance=(
                        "Call readSourceOutline without outlineIds to page through "
                        "the readable outline catalog, then reuse outlineId values."
                    ),
                    details={"invalidOutlineIds": sorted(outside)},
                )
            selected = requested or tuple(sorted(allowed))
            clauses.append("id IN (" + ",".join("?" for _ in selected) + ")")
            params.extend(selected)
        elif requested:
            clauses.append("id IN (" + ",".join("?" for _ in requested) + ")")
            params.extend(requested)
        cursor, limit = _page(arguments)
        if requested:
            cursor = 0
            limit = max(limit, len(requested))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, title, type, writing_chapter_id, markdown_content "
            "FROM outlines WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sort, id LIMIT ? OFFSET ?",
            params,
        )
        page = rows[:limit]
        if requested:
            found = {str(row["id"]) for row in rows}
            missing = set(requested) - found
            if missing:
                raise ScreenplayToolInputError(
                    "outlineIds contains identifiers outside the source book.",
                    guidance=(
                        "Use outlineId values returned by readSourceOutline for "
                        "this project."
                    ),
                    details={"invalidOutlineIds": sorted(missing)},
                )
        content_limit = max(2_000, 48_000 // max(1, len(page)))
        return {
            "outlines": [{
            "outlineId": str(row["id"]),
            **(
                {"chapterId": str(row["writing_chapter_id"])}
                if str(row.get("writing_chapter_id") or "").strip()
                else {}
            ),
            "title": str(row.get("title") or ""),
            "type": str(row.get("type") or ""),
            "content": _clip(row.get("markdown_content"), content_limit),
            } for row in page],
            "nextCursor": cursor + len(page),
            "hasMore": len(rows) > limit,
        }

    async def read_style(self, scope, _arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        row = await self._db.fetch_one(
            "SELECT * FROM book_style WHERE book_id = ?",
            [str(project["source_book_id"])],
        )
        allowed_chapter_ids = {
            str(item["id"])
            for item in await scoped_chapters(self._db, project)
        }
        references = tuple(
            chapter_id
            for chapter_id in _json_string_list(
                (row or {}).get("reference_chapter_ids")
            )
            if chapter_id in allowed_chapter_ids
        )
        return {"style": {
            "pov": str((row or {}).get("pov") or ""),
            "tone": str((row or {}).get("tone") or ""),
            "pace": str((row or {}).get("pace") or ""),
            "bannedRules": str((row or {}).get("banned_rules") or ""),
            "referenceChapterIds": list(references),
            "freeNotes": str((row or {}).get("free_notes") or ""),
        }}

    @staticmethod
    def source_refs(
        tool_name: str,
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        coverage = {
            "inspectSourceStructure": "catalog",
            "readSourceChapters": "full",
            "searchSourceText": "search",
            "listSourceCharacters": "catalog",
            "readSourceCharacters": "referenced",
            "listSourceWorldEntities": "catalog",
            "readSourceWorldEntities": "referenced",
            "readSourceBackground": "full",
            "querySourceStoryFacts": "search",
            "readSourceOutline": "referenced",
            "readSourceStyle": "full",
        }.get(tool_name)
        if coverage is None:
            return ()
        key, source_type, id_key, excerpt_key = {
            "inspectSourceStructure": (
                "chapters", "chapter", "chapterId", "title"
            ),
            "readSourceChapters": (
                "chapters", "chapter", "chapterId", "content"
            ),
            "searchSourceText": (
                "matches", "chapter", "chapterId", "excerpt"
            ),
            "listSourceCharacters": (
                "characters", "character", "characterId", "name"
            ),
            "readSourceCharacters": (
                "characters", "character", "characterId", "profile"
            ),
            "listSourceWorldEntities": (
                "entities", "world_entity", "entityId", "name"
            ),
            "readSourceWorldEntities": (
                "entities", "world_entity", "entityId", "profile"
            ),
            "querySourceStoryFacts": (
                "facts", "story_fact", "factId", "evidence"
            ),
            "readSourceOutline": (
                "outlines", "outline", "outlineId", "content"
            ),
        }.get(tool_name, ("", "", "", ""))
        if tool_name in {"readSourceBackground", "readSourceStyle"}:
            value = result.get(
                "content" if tool_name == "readSourceBackground" else "style"
            )
            return ({
                "sourceType": (
                    "story_background"
                    if tool_name == "readSourceBackground"
                    else "book_style"
                ),
                "sourceId": "main",
                "sourceRevision": _digest(value),
                "coverageMode": coverage,
                "excerpt": _clip(value, 1_000),
            },)
        items = result.get(key)
        if not isinstance(items, list):
            return ()
        refs = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            source_id = str(item.get(id_key) or "").strip()
            if not source_id:
                continue
            excerpt = item.get(excerpt_key)
            if isinstance(excerpt, Mapping):
                excerpt = excerpt.get("excerpt")
            refs.append({
                "sourceType": source_type,
                "sourceId": source_id,
                "sourceRevision": _digest(item),
                "coverageMode": coverage,
                "excerpt": _clip(excerpt, 1_000),
            })
        return tuple(refs)

    async def _project(self, scope) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_projects WHERE id = ?",
            [str(scope["projectId"])],
        )
        if row is None:
            raise LookupError("screenplay project does not exist")
        return dict(row)

    async def _source_project(self, scope) -> dict[str, Any]:
        project = await self._project(scope)
        book_id = str(project.get("source_book_id") or "").strip()
        if not book_id or book_id != str(scope.get("sourceBookId") or ""):
            raise ValueError("screenplay project has no bound source book")
        return project

    @staticmethod
    def _require_unscoped_catalog(project, label: str) -> None:
        if is_restricted_source_scope(project):
            raise ScreenplayToolInputError(
                f"{label} cannot be read directly for a chapter-restricted adaptation.",
                guidance=(
                    "Use querySourceStoryFacts to retrieve chapter-grounded "
                    "information within the licensed adaptation scope."
                ),
            )

    async def _revision_matches_role(
        self,
        project_id: str,
        revision_id: str,
        role: str,
    ) -> bool:
        return await self._db.fetch_one(
            "SELECT 1 FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
            [revision_id, project_id, role],
        ) is not None

    async def _outline_chapter_mappings(
        self,
        project: Mapping[str, Any],
        identifiers: set[str],
        *,
        allowed_chapter_ids: set[str],
    ) -> list[dict[str, str]]:
        if not identifiers:
            return []
        marks = ",".join("?" for _ in identifiers)
        rows = await self._db.fetch_all(
            "SELECT id, writing_chapter_id FROM outlines WHERE book_id = ? "
            f"AND id IN ({marks}) AND writing_chapter_id IS NOT NULL",
            [str(project["source_book_id"]), *sorted(identifiers)],
        )
        return [{
            "outlineId": str(row["id"]),
            "chapterId": str(row["writing_chapter_id"]),
        } for row in rows if str(row["writing_chapter_id"]) in allowed_chapter_ids]

    async def _rows_by_ids(
        self,
        table: str,
        book_id: str,
        ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            f"SELECT * FROM {table} WHERE book_id = ? AND id IN ({marks})",
            [book_id, *ids],
        )
        by_id = {int(row["id"]): row for row in rows}
        if set(ids) - set(by_id):
            raise ScreenplayToolInputError(
                "One or more requested entity identifiers are outside the source book.",
                guidance=(
                    "Use identifiers returned by the matching list tool for this "
                    "project; characterId and entityId are different namespaces."
                ),
                details={"invalidIds": sorted(set(ids) - set(by_id))},
            )
        return [by_id[item] for item in ids]


def _catalog_page(
    rows,
    cursor: int,
    limit: int,
    key: str,
    *,
    id_key: str,
) -> dict[str, Any]:
    page = rows[:limit]
    return {
        key: [{
            id_key: int(row["id"]),
            **{name: value for name, value in dict(row).items() if name != "id"},
        } for row in page],
        "nextCursor": cursor + len(page),
        "hasMore": len(rows) > limit,
    }


def _tool_scope_summary(scope_value: object) -> dict[str, Any]:
    summary = source_scope_summary(scope_value)
    for key in ("firstChapter", "lastChapter"):
        chapter = summary.get(key)
        if isinstance(chapter, Mapping) and chapter.get("id") is not None:
            summary[key] = {
                "chapterId": str(chapter["id"]),
                **{name: value for name, value in chapter.items() if name != "id"},
            }
    return summary


def _json_string_list(value: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(dict.fromkeys(
        text for item in parsed if (text := str(item).strip())
    ))


def _excerpt(text: str, query: str, radius: int = 500) -> str:
    index = text.lower().find(query.lower())
    if index < 0:
        return _clip(text, radius * 2)
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_payload(value: object, limit: int) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return payload
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "documentKind": payload.get("documentKind"),
        "truncated": True,
        "availableKeys": sorted(str(key) for key in payload),
        "episodeCount": (
            len(payload.get("episodes"))
            if isinstance(payload.get("episodes"), list)
            else None
        ),
        "sceneCount": (
            len(payload.get("scenes"))
            if isinstance(payload.get("scenes"), list)
            else None
        ),
        "guidance": (
            "Use episodeNumber or searchScreenplayDeliverables to read a "
            "smaller relevant part."
        ),
    }


def _continuity_only(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        key: value.get(key)
        for key in ("episodeNumber", "title", "continuitySummary")
        if value.get(key) is not None
    }


__all__ = ["ScreenplayToolQuery"]
