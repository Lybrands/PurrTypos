"""
URL normalisation — extracted from 4 duplicate occurrences of
``url.trim().replace(/\\/+$/, '')`` across openaiChat.js, anthropicChat.js,
and main.js.
"""

import re


def normalize_base_url(url: str | None) -> str | None:
    """Strip whitespace and trailing slashes.  Returns *None* when empty."""
    if not url:
        return None
    cleaned = re.sub(r"/+$", "", url.strip())
    return cleaned or None
