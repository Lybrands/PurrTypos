"""DeepSeek's standalone Search API; credentials never enter Agent receipts."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

import httpx

from domains.web_research import MAX_PAGE_CHARS, SearchHit, WebPage, WebResearchError, public_url, source_key

SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
MAX_RESPONSE_BYTES = 2_000_000


def require_api_key(api_key):
    if not api_key:
        raise WebResearchError("web_search_key_missing", "请在设置中配置 DeepSeek API Key 后使用联网搜索和网页读取。")


async def _post_json(api_key, endpoint, payload, *, reading=False):
    require_api_key(api_key)
    label = "网页读取" if reading else "搜索"
    unavailable = "web_page_unavailable" if reading else "web_search_unavailable"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            }, json=payload) as response:
                if response.status_code != 200:
                    code, message = {
                        401: ("web_search_auth_failed", "DeepSeek API Key 无效，请在设置中更新。"),
                        402: ("web_search_insufficient_balance", "DeepSeek 账户余额不足，请充值后重试。"),
                        403: ("web_url_blocked" if reading else unavailable, f"DeepSeek {label}被安全策略拒绝。"),
                        404: (unavailable, f"DeepSeek {label}未取得可用内容。"),
                        429: ("web_search_rate_limited", f"DeepSeek {label}受到限流，请稍后重试。"),
                    }.get(response.status_code, (unavailable, f"DeepSeek {label}返回 HTTP {response.status_code}，未取得可用结果。"))
                    raise WebResearchError(code, message)
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=32_768):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise WebResearchError(unavailable, f"DeepSeek {label}响应超过大小上限。")
        try:
            return json.loads(body)
        except (ValueError, UnicodeError) as error:
            raise WebResearchError(unavailable, f"DeepSeek 返回的{label}数据无法解析。") from error
    except httpx.TimeoutException as error:
        raise WebResearchError("web_page_timeout" if reading else "web_search_timeout", f"DeepSeek {label}超时，请稍后重试。") from error
    except (httpx.HTTPError, OSError) as error:
        raise WebResearchError(unavailable, f"DeepSeek {label}请求失败，请检查网络后重试。") from error


class DeepSeekWebSearch:
    def __init__(self, api_key):
        self._api_key = api_key

    @staticmethod
    def query_text(query, language):
        # Explicit lang: can express multiple languages and takes precedence.
        return query if re.search(r"(?:^|\s)lang:", query) or not language else f"{query} lang:{language}"

    @classmethod
    def validate_query(cls, query, language=None):
        if not 1 <= len(cls.query_text(query, language)) <= 128:
            raise WebResearchError("tool_input_invalid", "DeepSeek 检索词（含语言限定）最多 128 字符，请精简后重新查询。")

    def validate_request(self, query, language):
        self.validate_query(query, language)
        require_api_key(self._api_key)

    async def search(self, query, language, max_results):
        self.validate_request(query, language)
        payload = await _post_json(self._api_key, SEARCH_ENDPOINT, {
            "query": self.query_text(query, language), "count": max_results, "need_content": False,
        })
        return parse_search_response(json.dumps(payload), max_results)


class DeepSeekPageReader:
    def __init__(self, api_key):
        self._api_key = api_key

    def validate_request(self):
        require_api_key(self._api_key)

    async def read(self, url):
        # Only the fixed DeepSeek endpoint is contacted locally. Target-page DNS,
        # redirects and extraction are handled by DeepSeek's Fetch service.
        url = public_url(url)
        payload = await _post_json(self._api_key, "https://api.deepseek.com/v1/tools/fetch", {"url": url}, reading=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str) or not isinstance(payload.get("title", ""), str):
            raise WebResearchError("web_page_unavailable", "DeepSeek 返回的网页数据格式无效。")
        text = payload["content"].strip()
        if not text:
            raise WebResearchError("web_extraction_failed", "DeepSeek 未返回可用网页正文。")
        return WebPage(url, text[:MAX_PAGE_CHARS], len(text) > MAX_PAGE_CHARS, payload.get("title", "")[:300])


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
