"""Validate persisted search configuration and redact its write-only key."""
from domains.web_research import SETTING_KEY, API_KEY_SETTING, KEY_CONFIGURED_SETTING


def validate_search_settings(data):
    if SETTING_KEY in data and type(data[SETTING_KEY]) is not bool:
        raise ValueError("联网搜索开关必须为布尔值")
    if "web_search_provider" in data:
        raise ValueError("搜索服务固定为 DeepSeek，不再接受服务选择配置")
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
    result.pop("web_search_provider", None)
    return result
