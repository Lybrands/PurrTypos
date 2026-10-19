"""Free search and bounded, DNS-guarded public HTML fetching."""
from __future__ import annotations

import asyncio
import html
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from domains.web_research import MAX_PAGE_CHARS, SearchHit, WebPage, WebResearchError, public_ip, public_url

# DDGS is synchronous. Canceling a waiter must not free a slot while its native
# request is still running, otherwise repeated cancellation can grow a queue.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="web-research")
_SLOTS = threading.BoundedSemaphore(2)


async def _bounded_sync(operation):
    if not _SLOTS.acquire(blocking=False):
        raise WebResearchError("web_search_busy", "联网搜索繁忙，请稍后重试。")
    try:
        future = asyncio.get_running_loop().run_in_executor(_POOL, operation)
    except BaseException:
        _SLOTS.release()
        raise

    def finished(task):
        _SLOTS.release()
        if not task.cancelled():
            task.exception()  # Consume failures even when the waiter canceled.

    future.add_done_callback(finished)
    return await asyncio.shield(future)


class FreeWebSearch:
    async def search(self, query, language, max_results):
        try:
            hits = await self._rss(query, language, max_results)
            return hits, coverage_warnings(query, hits)
        except WebResearchError:
            stopped = threading.Event()
            try:
                hits, warnings = await _bounded_sync(lambda: self._search(query, language, max_results, stopped))
                return hits, ["Bing RSS 未返回可用结果，已使用免费后备搜索。", *warnings, *coverage_warnings(query, hits)]
            finally:
                stopped.set()

    @staticmethod
    async def _rss(query, language, max_results):
        import httpx
        try:
            import feedparser
        except ImportError as error:
            raise WebResearchError("web_dependency_missing", "搜索组件未安装，请更新后端依赖。") from error
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), trust_env=False) as client:
                async with client.stream("GET", "https://www.bing.com/search", params={
                    "q": query, "format": "rss", "mkt": "zh-CN" if language == "zh" else "en-US",
                }) as response:
                    if response.status_code != 200:
                        raise WebResearchError("web_search_unavailable", "Bing RSS 搜索暂时不可用。")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=32_768):
                        body.extend(chunk)
                        if len(body) > 1_000_000:
                            raise WebResearchError("web_search_unavailable", "搜索响应超过大小上限。")
            return parse_search_feed(bytes(body), max_results)
        except (httpx.HTTPError, OSError) as error:
            raise WebResearchError("web_search_unavailable", "Bing RSS 搜索请求失败。") from error

    @staticmethod
    def _search(query, language, max_results, stopped=None):
        try:
            from ddgs import DDGS
            from ddgs.engines import ENGINES
        except ImportError as error:
            raise WebResearchError("web_dependency_missing", "搜索组件未安装，请更新后端依赖。") from error
        region = "cn-zh" if language == "zh" else "us-en"
        failures = []
        # Explicit backends: no auto-selection of additional search services.
        for backend in ("duckduckgo", "brave"):
            if stopped is not None and stopped.is_set():
                raise WebResearchError("web_search_canceled", "搜索已取消，不再访问后备引擎。")
            # DDGS silently falls back to auto if an engine is absent/disabled.
            # Fail this configured attempt instead of widening query recipients.
            if backend not in ENGINES["text"]:
                failures.append(f"{backend}:disabled")
                continue
            try:
                rows = DDGS(timeout=6).text(
                    query, region=region, safesearch="moderate", max_results=max_results, backend=backend,
                )
            except Exception as error:
                # Provider failures must not masquerade as a successful empty
                # search. Details may contain URLs/proxies, so only expose type.
                failures.append(f"{backend}:{type(error).__name__}")
                continue
            hits, seen = [], set()
            for row in rows:
                try:
                    url = public_url(str(row.get("href") or ""))
                except WebResearchError:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                hits.append(SearchHit(str(row.get("title") or url)[:300], url, str(row.get("body") or "")[:1_000], backend))
                if len(hits) >= max_results:
                    break
            if hits:
                warnings = ["部分搜索后端不可用，已使用配置内的免费后端。"] if failures else []
                return hits, warnings
            failures.append(f"{backend}:empty_response")
        raise WebResearchError("web_search_unavailable", "免费搜索后端未返回可用结果，可能无匹配或受到网络/限流影响；不能据此断言不存在相关资料。")


def parse_search_feed(body, max_results):
    import feedparser
    feed = feedparser.parse(body)
    if not feed.version or feed.bozo:
        raise WebResearchError("web_search_unavailable", "搜索响应不是有效的 RSS 内容。")
    hits, seen = [], set()
    for entry in feed.entries:
        try:
            url = public_url(entry.get("link", ""))
        except WebResearchError:
            continue
        if url in seen:
            continue
        seen.add(url)
        plain = lambda value: html.unescape(re.sub(r"<[^>]*>", " ", value))
        hits.append(SearchHit(plain(entry.get("title", url))[:300], url, plain(entry.get("summary", ""))[:1_000], "bing_rss"))
        if len(hits) >= max_results:
            break
    if not hits:
        raise WebResearchError("web_search_unavailable", "RSS 搜索没有返回可用的公开网页。")
    return hits


def coverage_warnings(query, hits):
    # A narrow, explainable warning, not a quality score: spaced queries can
    # reveal that an engine matched only the broad entity and missed details.
    terms = {term.casefold() for term in re.findall(r"[^\W_]+", query) if len(term) >= 2}
    terms -= {"the", "and", "for", "with", "from", "of", "in", "to"}
    if len(terms) < 2 or not hits:
        return []
    best = max(sum(term in f"{hit.title} {hit.excerpt}".casefold() for term in terms) for hit in hits)
    if best * 2 < len(terms):
        return ["结果可能只匹配了部分关键词；请核对具体细节，必要时调整查询，不能据摘要确认事实。"]
    return []


class PublicResolver:
    """Resolve once, reject mixed public/private answers, connect to those IPs.

    aiohttp uses these numeric addresses for the connection, retaining the URL
    hostname for Host and TLS. There is no second unvalidated DNS lookup.
    """
    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await asyncio.get_running_loop().getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        if not rows or any(not public_ip(row[4][0]) for row in rows):
            raise WebResearchError("web_url_blocked", "网页域名解析到了非公开网络地址。")
        return [{"hostname": host, "host": row[4][0], "port": port, "family": row[0], "proto": row[2], "flags": socket.AI_NUMERICHOST} for row in rows]

    async def close(self):
        pass


class PublicPageReader:
    async def read(self, url):
        try:
            import aiohttp
            import trafilatura
        except ImportError as error:
            raise WebResearchError("web_dependency_missing", "网页读取组件未安装，请更新后端依赖。") from error
        target = public_url(url)
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=2)
        try:
            async with asyncio.timeout(25):
                async with aiohttp.ClientSession(
                    connector=connector, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=20, connect=5),
                    headers={"User-Agent": "PurrTypos/1.0 (public writing research)", "Accept": "text/html,text/plain;q=0.8"},
                ) as session:
                    for _ in range(5):
                        async with session.get(target, allow_redirects=False) as response:
                            if response.status in (301, 302, 303, 307, 308):
                                location = response.headers.get("Location")
                                if not location:
                                    raise WebResearchError("web_page_unavailable", "网页重定向缺少目标地址。")
                                target = public_url(urljoin(target, location))
                                continue
                            if response.status != 200:
                                raise WebResearchError("web_page_unavailable", f"网页返回 HTTP {response.status}，未读取正文。")
                            content_type = response.content_type
                            if content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                                raise WebResearchError("web_content_unsupported", "当前只读取 HTML 或纯文本网页，暂不支持 PDF、图片或下载文件。")
                            body = bytearray()
                            async for chunk in response.content.iter_chunked(32_768):
                                body.extend(chunk)
                                if len(body) > 2_000_000:
                                    raise WebResearchError("web_page_too_large", "网页超过 2 MB 读取上限。")
                            if content_type == "text/plain":
                                text = bytes(body).decode(response.charset or "utf-8", errors="replace")
                            else:
                                # Supply bytes only: extraction itself performs no fetch.
                                text = await _bounded_sync(lambda: trafilatura.extract(
                                    bytes(body), url=target, include_comments=False,
                                    include_tables=True, output_format="txt",
                                ))
                            if not text or len(text.strip()) < 80:
                                raise WebResearchError("web_extraction_failed", "未提取到足够正文，可能需要登录、执行脚本或通过访问验证。")
                            return WebPage(target, text[:MAX_PAGE_CHARS], len(text) > MAX_PAGE_CHARS)
                    raise WebResearchError("web_redirect_limit", "网页重定向次数超过上限。")
        except WebResearchError:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError, UnicodeError, LookupError) as error:
            # aiohttp may wrap resolver errors; do not disclose low-level URLs.
            cause = error
            for _ in range(5):
                if isinstance(cause, WebResearchError):
                    raise cause
                cause = cause.__cause__
                if cause is None:
                    break
            raise WebResearchError("web_page_unavailable", "网页请求超时或网络不可用，未读取正文。") from error
