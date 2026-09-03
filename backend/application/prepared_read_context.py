"""Shared host preparation of reusable, model-visible read results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from purra.context_budget import estimate_json_tokens
from purra.cancellation import raise_if_stopped
from purra.contracts import ContextBlock
from purra.evidence import CONTEXT_EVIDENCE_RECEIPTS_KEY
from domains.read_materials import READ_MATERIAL_SOURCE


_POLICY = (
    "已提供的读取结果可直接用于当前任务，无需再次调用相同读取工具。"
    "仅在所需材料缺失、范围不同或需要更新内容时调用读取工具。"
    "目录和摘要不能代替任务需要的正文。"
)


class PreparedReadContextProvider:
    def __init__(self, provider, load_materials):
        self.provider = provider
        self._load = load_materials

    async def build_context(self, request, budget, signal=None):
        bundle = await self.provider.build_context(request, budget, signal)
        return await self._prepare(request, budget, bundle, signal)

    async def build_planning_context(self, request, budget, signal=None):
        build = getattr(self.provider, "build_planning_context", self.provider.build_context)
        return await build(request, budget, signal)

    async def build_task_context(self, request, budget, task, signal=None):
        build = getattr(self.provider, "build_task_context", None)
        bundle = await (build(request, budget, task, signal) if build else
                        self.provider.build_context(request, budget, signal))
        return await self._prepare(request, budget, bundle, signal)

    async def _prepare(self, request, budget, bundle, signal):
        materials = await self._load(request, signal)
        available = max(0, budget.context_pool_tokens - sum(
            estimate_json_tokens(block.content) + 64 for block in bundle.blocks
        ) - estimate_json_tokens(_POLICY) - 128)
        blocks = list(bundle.blocks)
        existing = {block.name: block.content for block in blocks}
        existing.update({
            str(message.attributes.get("context_name")): message.content
            for message in request.messages
            if message.attributes.get("context_name")
        })
        added = 0
        for material in materials:
            raise_if_stopped(signal)
            digest = hashlib.sha256(material.content.encode()).hexdigest()
            identity = hashlib.sha256(f"{material.identity}:{digest}".encode()).hexdigest()
            name = f"read_material_{identity}"
            try:
                result = json.loads(material.content)
            except ValueError:
                result = material.content
            content = json.dumps({
                "tool": material.tool_name, "arguments": dict(material.arguments), "result": result,
            }, ensure_ascii=False, separators=(",", ":"))
            if name in existing and content in existing[name]:
                continue
            tokens = estimate_json_tokens(content) + 64
            if tokens > available:
                continue
            available -= tokens
            blocks.append(ContextBlock(
                name=name, content=content, token_count=tokens, untrusted=True,
                host_metadata={CONTEXT_EVIDENCE_RECEIPTS_KEY: [{
                    **dict(material.metadata), "evidenceId": identity,
                    "source": READ_MATERIAL_SOURCE, "itemId": material.identity,
                    "toolName": material.tool_name, "arguments": dict(material.arguments),
                    "contentDigest": digest, "complete": True,
                }]},
            ))
            existing[name] = content
            added += 1
        if added:
            blocks.append(ContextBlock(
                name="prepared_read_policy", content=_POLICY,
                token_count=estimate_json_tokens(_POLICY), untrusted=False,
            ))
        return replace(bundle, blocks=tuple(blocks))
