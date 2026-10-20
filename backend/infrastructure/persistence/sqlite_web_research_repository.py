"""Durable authorization, limits and replay for public-web tools."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from domains.web_research import (
    CONTRACT_VERSION, GRANT_KEY, MAX_READS, EXTENDED_MAX_READS, MAX_SEARCHES, SETTING_KEY,
    API_KEY_SETTING, SearchConfiguration, WebResearchError,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SqliteWebResearchRepository:
    def __init__(self, db):
        self.db = db

    async def enabled(self):
        row = await self.db.fetch_one("SELECT value FROM settings WHERE key = ?", [SETTING_KEY])
        return bool(row and row["value"] == "true")

    async def search_configuration(self):
        row = await self.db.fetch_one("SELECT value FROM settings WHERE key = ?", [API_KEY_SETTING])
        return SearchConfiguration(((row or {}).get("value") or "").strip())

    async def authorize(self, run_id):
        if not run_id or not await self.enabled():
            raise WebResearchError("web_search_disabled", "联网搜索已关闭。")
        run = await self.db.fetch_one(
            "SELECT binding_attributes_json, parent_run_id, status, mode FROM ai_agent_runs WHERE id = ?", [run_id],
        )
        attrs = json.loads((run or {}).get("binding_attributes_json") or "{}")
        permitted = attrs.get(GRANT_KEY) == {"version": CONTRACT_VERSION, "enabled": True}
        domain = attrs.get("domainNamespace")
        if not run or run["status"] != "running" or run["parent_run_id"] or not permitted or not (
            domain == "purrtypos.writing"
            or (domain == "purrtypos.screenplay" and run["mode"] == "screenplay_ordinary")
        ):
            raise WebResearchError("web_search_not_authorized", "本次任务未获得联网资料权限。")

    async def reserve(self, run_id, call_id, kind, arguments, *, external=True, read_limit=None):
        digest = hashlib.sha256(encode(arguments).encode()).hexdigest()
        async with self.db.transaction():
            await self.authorize(run_id)
            prior = await self.db.fetch_one(
                "SELECT * FROM web_research_attempts WHERE run_id = ? AND call_id = ?", [run_id, call_id],
            )
            if prior:
                if prior["arguments_digest"] != digest or prior["kind"] != kind:
                    raise WebResearchError("web_call_conflict", "联网调用标识与历史参数不一致。")
                if prior["result_json"] is not None:
                    return json.loads(prior["result_json"])
                raise WebResearchError("web_call_interrupted", "该请求曾中断，未自动重复访问；可在剩余额度内重新发起调用。")
            count = await self.db.fetch_one(
                "SELECT COUNT(*) AS n FROM web_research_attempts WHERE run_id = ? AND kind = ? AND external_request = 1", [run_id, kind],
            )
            limit = MAX_SEARCHES if kind == "search" else await self.read_limit(run_id)
            if external and int(count["n"]) >= limit:
                raise WebResearchError("web_budget_exhausted", "本轮联网调用已达上限，请依据已有资料回答并说明缺口。")
            await self.db.execute(
                "INSERT INTO web_research_attempts (run_id, call_id, kind, arguments_digest, external_request) VALUES (?, ?, ?, ?, ?)",
                [run_id, call_id, kind, digest, int(external)],
            )
            if read_limit is not None:
                if read_limit not in (MAX_READS, EXTENDED_MAX_READS):
                    raise ValueError("Unsupported web reading limit")
                await self.db.execute(
                    "INSERT INTO web_research_run_limits(run_id,read_limit) VALUES (?,?) "
                    "ON CONFLICT(run_id) DO UPDATE SET read_limit=MAX(read_limit,excluded.read_limit)",
                    [run_id, read_limit],
                )
        return None

    async def read_limit(self, run_id):
        row = await self.db.fetch_one("SELECT read_limit FROM web_research_run_limits WHERE run_id = ?", [run_id])
        return row["read_limit"] if row else MAX_READS

    async def budgets(self, run_id):
        rows = await self.db.fetch_all(
            "SELECT kind,COUNT(*) AS n FROM web_research_attempts WHERE run_id = ? "
            "AND external_request = 1 GROUP BY kind", [run_id],
        )
        used = {row["kind"]: row["n"] for row in rows}
        read_limit = await self.read_limit(run_id)
        return {
            "queryBudget": {"limit": MAX_SEARCHES, "used": used.get("search", 0), "remaining": max(0, MAX_SEARCHES - used.get("search", 0))},
            "readBudget": {"limit": read_limit, "used": used.get("read", 0), "remaining": max(0, read_limit - used.get("read", 0))},
        }

    async def finish(self, run_id, call_id, result):
        await self.db.execute(
            "UPDATE web_research_attempts SET result_json = ? WHERE run_id = ? AND call_id = ?",
            [encode(result), run_id, call_id],
        )

    async def reading_budgets(self, run_id):
        rows = await self.db.fetch_all(
            "SELECT DISTINCT json_extract(result_json, '$.bodyBudget') AS budget "
            "FROM web_research_attempts WHERE run_id = ? AND kind = 'read_batch' "
            "AND result_json IS NOT NULL AND json_extract(result_json, '$.bodyBudget') IS NOT NULL "
            "ORDER BY budget",
            [run_id],
        )
        return [json.loads(row["budget"]) for row in rows]

    async def add_source(self, run_id, hit):
        await self.db.execute(
            "INSERT INTO web_research_sources (id, run_id, url, title, excerpt, backend, retrieved_at, published_at, relevance_score, authority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id,url) DO UPDATE SET title=excluded.title,excerpt=excluded.excerpt,backend=excluded.backend,"
            "retrieved_at=excluded.retrieved_at,published_at=excluded.published_at,relevance_score=excluded.relevance_score,authority=excluded.authority",
            [f"web_{uuid4().hex}", run_id, hit.url, hit.title, hit.excerpt, hit.backend, now(), hit.published_at, hit.relevance_score, hit.authority],
        )
        return await self.db.fetch_one("SELECT * FROM web_research_sources WHERE run_id = ? AND url = ?", [run_id, hit.url])

    async def source(self, run_id, source_id):
        await self.authorize(run_id)
        row = await self.db.fetch_one("SELECT * FROM web_research_sources WHERE run_id = ? AND id = ?", [run_id, source_id])
        if row is None:
            raise WebResearchError("web_source_not_found", "网页来源不存在或不属于当前任务。")
        return row

    async def save_page(self, run_id, source_id, page):
        await self.db.execute(
            "UPDATE web_research_sources SET page_text = ?, page_retrieved_at = ?, page_truncated = ?, page_url = ?, "
            "title = CASE WHEN ? != '' THEN ? ELSE title END "
            "WHERE run_id = ? AND id = ? AND page_text IS NULL",
            [page.text, now(), int(page.truncated), page.url, page.title, page.title, run_id, source_id],
        )
