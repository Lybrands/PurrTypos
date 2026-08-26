"""Typed historical view of writing methods used by one persisted Run."""

from __future__ import annotations

from typing import Any

from purra.api import AgentExecutionCheckpoint
from purra.contracts import RunBinding
from purra.evidence import RunEvidenceStore
from purra.json_values import thaw_json_mapping

from domains.writing.method_resolution import WRITING_METHODS_CONTEXT


def read_writing_method_run_history(
    *,
    binding: RunBinding,
    checkpoint: AgentExecutionCheckpoint | None,
) -> dict[str, Any]:
    """Read binding and actual-use receipts through public PurrA contracts."""

    attributes = thaw_json_mapping(binding.attributes)
    frozen = attributes.get("writingMethodBindingSnapshot")
    snapshot = dict(frozen) if isinstance(frozen, dict) else {}
    store = RunEvidenceStore.from_checkpoint_mapping(
        thaw_json_mapping(checkpoint.evidence_state) if checkpoint else {}
    )
    receipts = tuple(
        receipt.to_mapping()
        for receipt in store.context_receipts()
        if receipt.context_block == WRITING_METHODS_CONTEXT
        and receipt.source == "writing_method_revision"
    )
    return {
        "bindingSnapshot": snapshot,
        "actualUsageReceipts": receipts,
    }


__all__ = ["read_writing_method_run_history"]
