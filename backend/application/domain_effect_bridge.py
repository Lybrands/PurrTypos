"""工具提交时机的进程内广播：our code, our timing.

工具函数在 host 进程内执行，提交成功的瞬间就是通知 UI 的正确时机——
无需把返回值交给框架再从其内部管道捞回（purra 的实时订阅流只放行
PUBLIC 事件，领域效果是 PRIVATE，走框架通道永远收不到）。

工具代码在提交成功后调用 ``notify_domain_effect()``；SSE 生成器通过
``register_queue()`` 注册输出队列，广播器把桥接块投递给每条已连接的流。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

EffectToChunk = Callable[[str, Any], dict[str, Any] | None]


class DomainEffectBroadcaster:
    """持有已连接 SSE 流的队列；发布时投递转换后的桥接块。"""

    def __init__(self, effect_to_chunk: EffectToChunk) -> None:
        self._effect_to_chunk = effect_to_chunk
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()

    def register(self, queue: asyncio.Queue[dict[str, Any]]) -> Callable[[], None]:
        """注册一条 SSE 输出队列；返回注销函数。"""
        self._queues.add(queue)
        return lambda: self._queues.discard(queue)

    def publish(self, run_id: str | None, effect_type: str, effect_payload: Any) -> None:
        chunk = self._effect_to_chunk(effect_type, effect_payload)
        if chunk is None:
            return
        for queue in list(self._queues):
            try:
                queue.put_nowait(chunk)
            except asyncio.QueueFull:
                # 断开或过慢的订阅者直接跳过：通知类块允许丢失，
                # 前端打开章节时始终会以服务端内容为准。
                continue


class _NullBroadcaster:
    """广播器未初始化时的空实现：通知缺失不允许影响工具执行。"""

    def register(self, queue):
        return lambda: None

    def publish(self, run_id, effect_type, effect_payload):
        return None


_broadcaster: DomainEffectBroadcaster | None = None


def init_broadcaster(effect_to_chunk: EffectToChunk) -> DomainEffectBroadcaster:
    global _broadcaster
    _broadcaster = DomainEffectBroadcaster(effect_to_chunk)
    return _broadcaster


def get_broadcaster() -> DomainEffectBroadcaster | _NullBroadcaster:
    """工具处理函数在提交成功后调用 publish 用的进程级广播器。"""
    return _broadcaster if _broadcaster is not None else _NullBroadcaster()
