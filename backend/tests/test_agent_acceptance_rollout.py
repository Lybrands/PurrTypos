from __future__ import annotations

from pathlib import Path

import pytest

from agents.shared.acceptance_rollout import (
    NOVEL_ANALYSIS_ACCEPTANCE_ENV,
    NOVEL_ANALYSIS_ACCEPTANCE_MARKER,
    SCREENPLAY_ACCEPTANCE_ENV,
    SCREENPLAY_ACCEPTANCE_MARKER,
    WRITING_ACCEPTANCE_ENV,
    WRITING_ACCEPTANCE_MARKER,
    resolve_process_rollout_policy,
)
from agents.shared.implementation import AgentKind


def test_process_rollout_enables_all_replacements_by_default(
    tmp_path,
) -> None:
    policy = resolve_process_rollout_policy(data_dir=tmp_path, environ={})

    assert policy.replacement_agent_kinds == frozenset({
        AgentKind.WRITING,
        AgentKind.NOVEL_ANALYSIS,
        AgentKind.SCREENPLAY,
    })


@pytest.mark.parametrize("data_dir", [None, Path("")])
def test_acceptance_flag_rejects_an_implicit_data_directory(data_dir) -> None:
    with pytest.raises(RuntimeError, match="explicit data directory"):
        resolve_process_rollout_policy(
            data_dir=data_dir,
            environ={NOVEL_ANALYSIS_ACCEPTANCE_ENV: "1"},
        )


def test_acceptance_flag_rejects_an_unmarked_data_directory(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="is not marked"):
        resolve_process_rollout_policy(
            data_dir=tmp_path,
            environ={NOVEL_ANALYSIS_ACCEPTANCE_ENV: "1"},
        )


def test_marked_analysis_acceptance_directory_only_validates_isolation(tmp_path) -> None:
    (tmp_path / NOVEL_ANALYSIS_ACCEPTANCE_MARKER).touch()

    policy = resolve_process_rollout_policy(
        data_dir=tmp_path,
        environ={NOVEL_ANALYSIS_ACCEPTANCE_ENV: "1"},
    )

    assert policy.replacement_agent_kinds == frozenset({
        AgentKind.WRITING,
        AgentKind.NOVEL_ANALYSIS,
        AgentKind.SCREENPLAY,
    })


def test_marked_acceptance_directory_enables_screenplay_only(tmp_path) -> None:
    (tmp_path / SCREENPLAY_ACCEPTANCE_MARKER).touch()

    policy = resolve_process_rollout_policy(
        data_dir=tmp_path,
        environ={SCREENPLAY_ACCEPTANCE_ENV: "1"},
    )

    assert policy.replacement_agent_kinds == frozenset({
        AgentKind.WRITING,
        AgentKind.NOVEL_ANALYSIS,
        AgentKind.SCREENPLAY,
    })


def test_screenplay_acceptance_flag_rejects_unmarked_directory(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Screenplay.*not marked"):
        resolve_process_rollout_policy(
            data_dir=tmp_path,
            environ={SCREENPLAY_ACCEPTANCE_ENV: "1"},
        )


def test_marked_acceptance_directory_enables_writing_only(tmp_path) -> None:
    (tmp_path / WRITING_ACCEPTANCE_MARKER).touch()

    policy = resolve_process_rollout_policy(
        data_dir=tmp_path,
        environ={WRITING_ACCEPTANCE_ENV: "1"},
    )

    assert policy.replacement_agent_kinds == frozenset({
        AgentKind.WRITING,
        AgentKind.NOVEL_ANALYSIS,
        AgentKind.SCREENPLAY,
    })


def test_writing_acceptance_flag_rejects_unmarked_directory(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Writing.*not marked"):
        resolve_process_rollout_policy(
            data_dir=tmp_path,
            environ={WRITING_ACCEPTANCE_ENV: "1"},
        )
