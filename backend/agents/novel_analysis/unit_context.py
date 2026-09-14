"""Canonical host-owned context for one replacement analysis Unit call."""

from __future__ import annotations

from dataclasses import dataclass

from agents.novel_analysis.observation_tools import validate_observation_directory
from agents.novel_analysis.recipe import AnalysisUnitKind
from purra.json_values import thaw_json_mapping


_OBSERVATION_UNITS = frozenset({
    AnalysisUnitKind.NORMALIZE,
    AnalysisUnitKind.OVERVIEW,
    AnalysisUnitKind.DISTILL_TECHNIQUE,
})


@dataclass(frozen=True, slots=True)
class NovelAnalysisUnitContext:
    kind: AnalysisUnitKind
    observations: tuple[dict[str, object], ...] = ()

    @classmethod
    def from_request(cls, request) -> "NovelAnalysisUnitContext | None":
        payload = thaw_json_mapping(request.domain_context.payload)
        raw = payload.get("unit")
        if raw is None:
            return None
        if not isinstance(raw, dict) or set(raw) != {"kind", "observations"}:
            raise ValueError("replacement analysis unit context shape is invalid")
        kind = AnalysisUnitKind(raw["kind"])
        observations = validate_observation_directory(raw["observations"])
        if kind not in _OBSERVATION_UNITS and observations:
            raise ValueError("replacement analysis unit cannot receive observations")
        return cls(kind=kind, observations=observations)


__all__ = ["NovelAnalysisUnitContext"]
