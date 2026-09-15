"""Complete Unit executor for the scalable Novel Analysis recipe."""

from __future__ import annotations

from agents.novel_analysis.coverage_execution import (
    ScalableCoverageError,
    ScalableCoverageUnitExecutor,
)
from agents.novel_analysis.map_execution import (
    PurrAScalableMapChildRunner,
    ScalableMapExecutionError,
    ScalableMapOutputError,
    ScalableMapUnitExecutor,
)
from agents.novel_analysis.reduce_execution import (
    PurrAScalableReduceChildRunner,
    ScalableReduceExecutionError,
    ScalableReduceOutputError,
    ScalableReduceUnitExecutor,
)
from agents.novel_analysis.review_execution import (
    PurrAScalableReviewChildRunner,
    ScalableReviewExecutionError,
    ScalableReviewOutputError,
    ScalableReviewUnitExecutor,
)
from agents.novel_analysis.skill_creation import (
    PurrAScalableSkillChildRunner,
    ScalableSkillCreationError,
    ScalableSkillOutputError,
    ScalableSkillUnitExecutor,
)
from agents.novel_analysis.synthesize_execution import (
    PurrAScalableSynthesisChildRunner,
    ScalableSynthesisExecutionError,
    ScalableSynthesisOutputError,
    ScalableSynthesisUnitExecutor,
)
from purra.recovery import FailureCategory, FailureSignal


class NovelAnalysisStageOutputError(RuntimeError):
    """The analysis result is durable, but its public narration was not delivered."""

    code = "novel_analysis_stage_output_failed"


class ScalableNovelAnalysisUnitExecutor:
    """Route Host-expanded Units without introducing another execution path."""

    def __init__(self, db, *, model_name: str, stage_output=None) -> None:
        self._stage_output = stage_output
        self._map = ScalableMapUnitExecutor(
            db,
            child_runner=PurrAScalableMapChildRunner(db, model_name=model_name),
        )
        self._reduce = ScalableReduceUnitExecutor(
            db,
            child_runner=PurrAScalableReduceChildRunner(db, model_name=model_name),
        )
        self._synthesis = ScalableSynthesisUnitExecutor(
            db,
            child_runner=PurrAScalableSynthesisChildRunner(db, model_name=model_name),
        )
        self._coverage = ScalableCoverageUnitExecutor(db)
        self._skill = ScalableSkillUnitExecutor(
            db,
            child_runner=PurrAScalableSkillChildRunner(db, model_name=model_name),
        )
        self._review = ScalableReviewUnitExecutor(
            db,
            child_runner=PurrAScalableReviewChildRunner(db, model_name=model_name),
        )

    def bind_agent_core(self, core) -> None:
        self._map.bind_agent_core(core)
        self._reduce.bind_agent_core(core)
        self._synthesis.bind_agent_core(core)
        self._skill.bind_agent_core(core)
        self._review.bind_agent_core(core)

    async def execute(self, context, signal=None):
        kind = str(context.unit.metadata.get("unitKind") or "")
        executor = {
            "map": self._map,
            "reduce": self._reduce,
            "synthesize": self._synthesis,
            "coverage": self._coverage,
            "skill": self._skill,
            "review": self._review,
        }.get(kind)
        if executor is None:
            raise ValueError(f"unsupported scalable analysis Unit kind: {kind}")
        result = await executor.execute(context, signal)
        if self._stage_output is not None:
            try:
                await self._stage_output.publish(context, result, signal)
            except Exception as error:
                raise NovelAnalysisStageOutputError(
                    "failed to publish durable novel-analysis stage output"
                ) from error
        return result

    def classify_failure(self, error):
        if isinstance(error, NovelAnalysisStageOutputError):
            return FailureSignal(
                category=FailureCategory.TOOL_EXECUTION,
                code=error.code,
                retryable=True,
            )
        routes = (
            ((ScalableMapExecutionError, ScalableMapOutputError), self._map),
            ((ScalableReduceExecutionError, ScalableReduceOutputError), self._reduce),
            ((ScalableSynthesisExecutionError, ScalableSynthesisOutputError), self._synthesis),
            ((ScalableCoverageError,), self._coverage),
            ((ScalableSkillCreationError, ScalableSkillOutputError), self._skill),
            ((ScalableReviewExecutionError, ScalableReviewOutputError), self._review),
        )
        for error_types, executor in routes:
            if isinstance(error, error_types):
                return executor.classify_failure(error)
        return self._map.classify_failure(error)


__all__ = [
    "NovelAnalysisStageOutputError",
    "ScalableNovelAnalysisUnitExecutor",
]
