"""Explicit dispatch for novel and memory evidence, including tool replay."""
from dataclasses import dataclass
from domains.writing.knowledge import KnowledgeError


@dataclass(frozen=True)
class NovelKnowledgeEvidenceValidator:
    knowledge: object
    memory: object
    book_id: str
    scope: object = None

    async def validate_evidence(self, receipts, *, signal=None):
        if self.scope:
            await self.knowledge.check_scope(self.book_id, dict(self.scope))
        relevant = [r for r in receipts if r.source.startswith('novel_knowledge')]
        if any(r.source != 'novel_knowledge/v1' for r in relevant):
            raise KnowledgeError('knowledge_evidence_namespace_unsupported')
        if relevant:
            # Also catches newly introduced duplicate IDs or ownership overlap before reuse.
            await self.knowledge.refresh(self.book_id)
            await self.knowledge.validate(self.book_id, relevant, signal=signal)
        await self.memory.validate_evidence(receipts, signal=signal)
