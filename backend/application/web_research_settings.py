"""Validate persisted search configuration and redact its write-only key."""
from domains.web_research import SETTING_KEY, PROVIDER_SETTING, API_KEY_SETTING, KEY_CONFIGURED_SETTING


def validate_search_settings(data):
    if SETTING_KEY in data and type(data[SETTING_KEY]) is not bool:
        raise ValueError("联网搜索开关必须为布尔值")
    if PROVIDER_SETTING in data and data[PROVIDER_SETTING] not in ("deepseek", "free"):
        raise ValueError("搜索服务必须为 deepseek 或 free")
    normalized = dict(data)
    normalized.pop(KEY_CONFIGURED_SETTING, None)
    if API_KEY_SETTING in data:
        key = data[API_KEY_SETTING]
        if not isinstance(key, str) or len(key) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in key.strip()):
            raise ValueError("DeepSeek API Key 必须为不含空白的 ASCII 字符串，清空请传空字符串")
        normalized[API_KEY_SETTING] = key.strip()
    return normalized


def public_search_settings(data):
    result = dict(data)
    key = result.pop(API_KEY_SETTING, "")
    result[KEY_CONFIGURED_SETTING] = bool(isinstance(key, str) and key.strip())
    if result.get(PROVIDER_SETTING) not in ("deepseek", "free"):
        result[PROVIDER_SETTING] = "deepseek"
    return result
