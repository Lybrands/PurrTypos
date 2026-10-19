"""Public-web evidence types and input rules, independent of Agent products."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SETTING_KEY = "web_search_enabled"
GRANT_KEY = "webResearch"
CONTRACT_VERSION = 1
MAX_SEARCHES = 6
MAX_READS = 8
EXTENDED_MAX_READS = 12
MAX_SEARCH_QUERIES = 3
DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
SEARCH_EXCERPT_CHARS = 300
SEARCH_CONCURRENCY = 2
MAX_PAGE_CHARS = 24_000
PAGE_CHARS = 4_000
MAX_BATCH_PAGES = 5
MAX_BATCH_BODY_CHARS = 8_000
PAGE_READ_CONCURRENCY = 2


def source_key(value: str) -> str:
    """Deduplicate tracking variants without changing the URL used to read."""
    parsed = urlsplit(public_url(value))
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if parsed.port and parsed.port != (443 if parsed.scheme == "https" else 80):
        host += f":{parsed.port}"
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid"}]
    return urlunsplit((parsed.scheme, host, parsed.path, urlencode(sorted(query)), ""))


class WebResearchError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    excerpt: str
    backend: str


@dataclass(frozen=True)
class WebPage:
    url: str
    text: str
    truncated: bool = False


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # IPv4-mapped/transition IPv6 can otherwise hide a private IPv4 endpoint.
    if isinstance(address, ipaddress.IPv6Address):
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
        if address.ipv4_mapped is not None:
            return public_ip(str(address.ipv4_mapped))
        if address.sixtofour is not None or address.teredo is not None:
            return False
    return address.is_global and not address.is_multicast


def public_url(value: str) -> str:
    try:
        if not isinstance(value, str) or not value or len(value) > 2_048:
            raise ValueError
        if any(ord(c) < 33 for c in value) or "\\" in value:
            raise ValueError
        url = urlsplit(value)
        host = (url.hostname or "").rstrip(".").lower()
        if url.scheme not in ("https", "http") or not host or url.username is not None or url.password is not None:
            raise ValueError
        if url.port not in (None, 80, 443):
            raise ValueError
        if "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if all(re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", part) for part in host.split(".")):
                raise ValueError
            host.encode("idna")
        else:
            if not public_ip(host):
                raise ValueError
        return urlunsplit((url.scheme, url.netloc, url.path or "/", url.query, ""))
    except (ValueError, UnicodeError) as error:
        raise WebResearchError("web_url_blocked", "仅允许不含凭据的公开 HTTP(S) 网页。") from error


def request_allows_web(request) -> bool:
    namespace = request.domain_context.namespace
    return bool(
        request.tools_enabled
        and request.metadata.get(GRANT_KEY) == {"version": CONTRACT_VERSION, "enabled": True}
        and not request.metadata.get("parentRunId")
        and request.metadata.get("responseAudience") != "internal"
        and (
            namespace == "purrtypos.writing"
            or (namespace == "purrtypos.screenplay" and request.mode == "screenplay_ordinary")
        )
    )
