"""Session title policy using the shared bounded model request service."""

from types import SimpleNamespace
from application.model_request_service import BackgroundModelContext, ModelRequestService
from utils.session_title import SESSION_TITLE_SYSTEM_PROMPT, normalize_session_title


async def generate_session_title(*, api_key, provider, options, prompt, db=None):
    runtime = SimpleNamespace(apiProvider=provider, baseURL=options.get("baseURL"),
                              contextWindow=options.get("context_window"), options=options)
    result = await ModelRequestService().complete(
        api_key=api_key, runtime=runtime, context=BackgroundModelContext("session_title", 32),
        db=db,
        messages=[{"role": "system", "content": SESSION_TITLE_SYSTEM_PROMPT},
                  {"role": "user", "content": str(prompt or "").strip()}],
    )
    return normalize_session_title(str(result.message.content or ""))
