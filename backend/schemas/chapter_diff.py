from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CommitDiffRequest(BaseModel):
    """提交 diff 后落盘：保存 articles 同时记录历史。

    ``content`` 是用户接受/拒绝后的最终 Lexical JSON 字符串（与 articles.content 同 schema），
    后端原样写入 articles.content 并保留 ``before_text/after_text`` 纯文本镜像供回滚显示。
    """

    content: str
    before_text: str = ""
    after_text: str = ""
    source: str = "ai_rewrite"
    accepted_segments: int = 0
    rejected_segments: int = 0
