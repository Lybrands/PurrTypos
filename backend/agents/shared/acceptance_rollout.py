"""Process-level replacement rollout and isolated acceptance policy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from agents.shared.implementation import AgentKind
from agents.shared.implementation_registry import AgentRolloutPolicy


NOVEL_ANALYSIS_ACCEPTANCE_ENV = (
    "PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE"
)
NOVEL_ANALYSIS_ACCEPTANCE_MARKER = (
    ".purrtypos-novel-analysis-replacement-acceptance"
)
SCREENPLAY_ACCEPTANCE_ENV = "PURRTYPOS_SCREENPLAY_REPLACEMENT_ACCEPTANCE"
SCREENPLAY_ACCEPTANCE_MARKER = ".purrtypos-screenplay-replacement-acceptance"
WRITING_ACCEPTANCE_ENV = "PURRTYPOS_WRITING_REPLACEMENT_ACCEPTANCE"
WRITING_ACCEPTANCE_MARKER = ".purrtypos-writing-replacement-acceptance"
_ENABLED_FLAG_VALUES = frozenset({"1", "true", "yes", "on"})


def resolve_process_rollout_policy(
    *,
    data_dir: Path | None,
    environ: Mapping[str, str] | None = None,
) -> AgentRolloutPolicy:
    """Select production replacements and validate isolated acceptance modes.

    All three product Agents are production replacements. Acceptance flags
    only activate isolation safeguards; they never select an Agent
    implementation. There is no legacy create fallback.
    """

    values = os.environ if environ is None else environ
    replacement_kinds = {
        AgentKind.WRITING,
        AgentKind.NOVEL_ANALYSIS,
        AgentKind.SCREENPLAY,
    }
    screenplay_acceptance = _enabled(values, SCREENPLAY_ACCEPTANCE_ENV)

    for env_name, marker_name, label in (
        (
            NOVEL_ANALYSIS_ACCEPTANCE_ENV,
            NOVEL_ANALYSIS_ACCEPTANCE_MARKER,
            "Novel Analysis",
        ),
        (
            WRITING_ACCEPTANCE_ENV,
            WRITING_ACCEPTANCE_MARKER,
            "Writing",
        ),
    ):
        if not _enabled(values, env_name):
            continue
        _require_marked_acceptance_directory(data_dir, marker_name, label)
    if screenplay_acceptance:
        _require_marked_acceptance_directory(
            data_dir,
            SCREENPLAY_ACCEPTANCE_MARKER,
            "Screenplay",
        )
    return AgentRolloutPolicy(frozenset(replacement_kinds))


def _enabled(values: Mapping[str, str], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() in _ENABLED_FLAG_VALUES


def _require_marked_acceptance_directory(
    data_dir: Path | None,
    marker_name: str,
    label: str,
) -> None:
    if data_dir is None or str(data_dir).strip() in {"", "."}:
        raise RuntimeError(
            f"{label} replacement acceptance requires an explicit data directory"
        )
    marker = data_dir.resolve() / marker_name
    if not marker.is_file():
        raise RuntimeError(
            f"{label} replacement acceptance data directory is not marked"
        )


__all__ = [
    "NOVEL_ANALYSIS_ACCEPTANCE_ENV",
    "NOVEL_ANALYSIS_ACCEPTANCE_MARKER",
    "SCREENPLAY_ACCEPTANCE_ENV",
    "SCREENPLAY_ACCEPTANCE_MARKER",
    "WRITING_ACCEPTANCE_ENV",
    "WRITING_ACCEPTANCE_MARKER",
    "resolve_process_rollout_policy",
]
