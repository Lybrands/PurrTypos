"""Compatibility registration entrypoint for book-context tool handlers.

The implementations live in domain-focused modules.  ``tool_executor`` keeps
importing this module so existing import paths continue to trigger every
handler registration exactly once.
"""

from services.tool_handlers import book_style_tools as _book_style_tools  # noqa: F401
from services.tool_handlers import character_tools as _character_tools  # noqa: F401
from services.tool_handlers import setting_entity_tools as _setting_entity_tools  # noqa: F401
from services.tool_handlers import story_background_tools as _story_background_tools  # noqa: F401
