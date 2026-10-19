"""DeepSeek's standalone Search API; credentials never enter Agent receipts."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import httpx

from domains.web_research import SearchHit, WebResearchError, public_url, source_key

SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
MAX_RESPONSE_BYTES = 2_000_000


class DeepSeekWebSearch:
    def __init__(self, api_key):
        self._api_key = api_key

    @staticmethod
    def validate_query(query):
        if not 1 <= len(query) <= 128:
            raise WebResearchError("tool_input_invalid", "DeepSeek 检索词最多 128 字符，请精简后重新查询。")

    async def search(self, query, language, max_results):
        self.validate_query(query)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False) as client:
                async with client.stream("POST", SEARCH_ENDPOINT, headers={
                    "Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json",
                }, json={"query": query, "count": max_results, "need_content": False}) as response:
                    if response.status_code != 200:
                        code, message = {
                            401: ("web_search_auth_failed", "DeepSeek API Key 无效，请在设置中更新。"),
                            402: ("web_search_insufficient_balance", "DeepSeek 账户余额不足，请充值或在设置中选择免费搜索。"),
                            429: ("web_search_rate_limited", "DeepSeek 搜索受到限流，请稍后重试。"),
                        }.get(response.status_code, ("web_search_unavailable", f"DeepSeek 搜索返回 HTTP {response.status_code}，未取得可用结果。"))
                        raise WebResearchError(code, message)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=32_768):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise WebResearchError("web_search_unavailable", "DeepSeek 搜索响应超过大小上限。")
            return parse_search_response(bytes(body), max_results)
        except httpx.TimeoutException as error:
            raise WebResearchError("web_search_timeout", "DeepSeek 搜索超时，请稍后重试。") from error
        except (httpx.HTTPError, OSError) as error:
            raise WebResearchError("web_search_unavailable", "DeepSeek 搜索请求失败，请检查网络后重试。") from error


def parse_search_response(body, max_results):
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise WebResearchError("web_search_unavailable", "DeepSeek 返回的搜索数据无法解析。") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise WebResearchError("web_search_unavailable", "DeepSeek 返回的搜索数据格式无效。")
    hits, seen, skipped = [], set(), False
    for row in payload["results"][:50]:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not isinstance(row.get("summary", ""), str) or not isinstance(row.get("snippet", ""), str):
            skipped = True
            continue
        try:
            url = public_url(row.get("url"))
        except WebResearchError:
            skipped = True
            continue
        key = source_key(url)
        if key in seen:
            continue
        seen.add(key)
        score = row.get("rerank_score")
        score = float(score) if type(score) in (int, float) and 0 <= score <= 3 and math.isfinite(score) else None
        authority = row.get("authority")
        authority = authority if type(authority) is int and -1 <= authority <= 4 else None
        hits.append(SearchHit(
            row["title"][:300] or url, url, (row.get("summary") or row.get("snippet") or "")[:1_000],
            "deepseek", _publication_time(row.get("date")), score, authority,
        ))
        if len(hits) >= max_results:
            break
    if payload["results"] and not hits:
        raise WebResearchError("web_search_unavailable", "DeepSeek 结果未包含可用的公开网页来源。")
    return hits, (["部分 DeepSeek 结果格式无效或不是公开网页，已过滤。"] if skipped else [])


def _publication_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc).isoformat() if date.tzinfo is not None else None
    except ValueError:
        return None
