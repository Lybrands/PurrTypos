"""
Random ID generation — port of electron/idUtils.js.
"""

import secrets

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def short_id8() -> str:
    """8-char random id from a 62-char alphabet (crypto-quality)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))
