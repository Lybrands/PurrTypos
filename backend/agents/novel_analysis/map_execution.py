"""Bounded source tool and Child-owned executor for scalable Map Units."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.child_submission import (
    NovelAnalysisChildSubmissionStore,
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from infrastructure.persistence.sqlite_run_tree_repository import (
    SqliteRunTreeRepository,
)
from purra.agent_tree import (
    AgentCapabilityGrant,
    ChildAgentSpec,
    ContinueAgentCommand,
    SpawnAgentsCommand,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.long_tasks import DurableUnitExecutionContext, LongTaskUnitResult
from purra.ports import ToolRegistration
from purra.recovery import FailureCategory, FailureSignal
from purra.tools import InMemoryToolCatalog


READ_NOVEL_SOURCE_SLICE = "readNovelSourceSlice"
SCALABLE_MAP_SCOPE_STATE_KEY = "novelAnalysisMapSliceScope"
SCALABLE_MAP_OUTPUT_SCHEMA_VERSION = 1


class ScalableMapExecutionError(ValueError):
    code = "novel_analysis_map_execution_invalid"


class ScalableMapOutputError(ValueError):
    code = "novel_analysis_map_output_invalid"


class ScalableChildRunError(RuntimeError):
    def __init__(self, code: str, label: str) -> None:
        self.code = str(code or "novel_analysis_child_failed")[:240]
        super().__init__(f"{label} Child failed: {self.code}")


def raise_child_run_failure(aggregation, child_run_id: str, label: str) -> None:
    item = next((
        item for item in aggregation.results
        if item.get("runId") == child_run_id
    ), None)
    code = item.get("errorCode") if isinstance(item, Mapping) else None
    raise ScalableChildRunError(str(code or "novel_analysis_child_failed"), label)


@dataclass(frozen=True, slots=True)
class MapSliceRange:
    section_id: str
    section_ordinal: int
    start_character: int
    end_character: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class MapSliceScope:
    source_revision_id: str
    source_revision_digest: str
    slice_id: str
    slice_position: int
    token_count: int
    ranges: tuple[MapSliceRange, ...]

    @classmethod
    def from_manifest(cls, manifest: object, slice_id: object) -> "MapSliceScope":
        if not isinstance(manifest, Mapping):
            raise ScalableMapExecutionError("Map SliceManifest is unavailable")
        normalized = str(slice_id or "").strip()
        slices = manifest.get("slices")
        if not isinstance(slices, Sequence) or isinstance(slices, (str, bytes)):
            raise ScalableMapExecutionError("Map SliceManifest has no slices")
        raw = next((item for item in slices if (
            isinstance(item, Mapping) and item.get("sliceId") == normalized
        )), None)
        if raw is None:
            raise ScalableMapExecutionError("Map Unit references an unknown slice")
        raw_ranges = raw.get("ranges")
        if not isinstance(raw_ranges, Sequence) or isinstance(raw_ranges, (str, bytes)):
            raise ScalableMapExecutionError("Map slice ranges are unavailable")
        ranges = []
        for item in raw_ranges:
            if not isinstance(item, Mapping):
                raise ScalableMapExecutionError("Map slice range is invalid")
            try:
                source_range = MapSliceRange(
                    section_id=str(item["sectionId"]),
                    section_ordinal=int(item["sectionOrdinal"]),
                    start_character=int(item["startCharacter"]),
                    end_character=int(item["endCharacter"]),
                    content_digest=str(item["contentDigest"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ScalableMapExecutionError("Map slice range is invalid") from error
            if (
                not source_range.section_id
                or not source_range.content_digest
                or source_range.section_ordinal < 0
                or source_range.start_character < 0
                or source_range.end_character <= source_range.start_character
            ):
                raise ScalableMapExecutionError("Map slice range is invalid")
            ranges.append(source_range)
        try:
            scope = cls(
                source_revision_id=str(manifest["sourceRevisionId"]),
                source_revision_digest=str(manifest["sourceRevisionDigest"]),
                slice_id=normalized,
                slice_position=int(raw["position"]),
                token_count=int(raw["tokenCount"]),
                ranges=tuple(ranges),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ScalableMapExecutionError("Map slice identity is invalid") from error
        if (
            not scope.source_revision_id
            or not scope.source_revision_digest
            or not scope.slice_id
            or scope.slice_position < 0
            or scope.token_count < 0
            or not scope.ranges
        ):
            raise ScalableMapExecutionError("Map slice identity is invalid")
        return scope

    def to_mapping(self) -> dict[str, object]:
        return {
            "sourceRevisionId": self.source_revision_id,
            "sourceRevisionDigest": self.source_revision_digest,
            "sliceId": self.slice_id,
            "slicePosition": self.slice_position,
            "tokenCount": self.token_count,
            "ranges": [{
                "sectionId": item.section_id,
                "sectionOrdinal": item.section_ordinal,
                "startCharacter": item.start_character,
                "endCharacter": item.end_character,
                "contentDigest": item.content_digest,
            } for item in self.ranges],
        }


class SqliteNovelSourceSliceReader:
    def __init__(self, db) -> None:
        self._db = db

    async def read(self, scope: MapSliceScope) -> dict[str, object]:
        revision = await self._db.fetch_one(
            "SELECT content_digest FROM novel_source_revisions WHERE id = ?",
            [scope.source_revision_id],
        )
        if revision is None or revision["content_digest"] != scope.source_revision_digest:
            raise ScalableMapExecutionError("Map source revision changed after admission")
        items = []
        for source_range in scope.ranges:
            row = await self._db.fetch_one(
                "SELECT ordinal, title, text_content, content_digest "
                "FROM novel_source_sections WHERE revision_id = ? AND id = ?",
                [scope.source_revision_id, source_range.section_id],
            )
            if row is None or (
                int(row["ordinal"]) != source_range.section_ordinal
                or str(row["content_digest"]) != source_range.content_digest
            ):
                raise ScalableMapExecutionError("Map source range changed after admission")
            text = str(row["text_content"])
            if source_range.end_character > len(text):
                raise ScalableMapExecutionError("Map source range exceeds its section")
            items.append({
                "sectionId": source_range.section_id,
                "sectionOrdinal": source_range.section_ordinal,
                "sectionTitle": str(row["title"]),
                "startCharacter": source_range.start_character,
                "endCharacter": source_range.end_character,
                "text": text[source_range.start_character:source_range.end_character],
            })
        return {
            "sourceRevisionId": scope.source_revision_id,
            "sliceId": scope.slice_id,
            "slicePosition": scope.slice_position,
            "tokenCount": scope.token_count,
            "items": items,
        }


def build_scalable_map_source_tool_catalog(
    db, *, run_tree_repository=None
) -> InMemoryToolCatalog:
    reader = SqliteNovelSourceSliceReader(db)
    tree = run_tree_repository or SqliteRunTreeRepository(db)

    async def read_slice(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        try:
            scope = await _scope_for_tool_state(state, tree)
            payload = await reader.read(scope)
            raise_if_stopped(signal)
            return ToolHandlerResult(content=json.dumps(
                payload, ensure_ascii=False, allow_nan=False
            ))
        except ScalableMapExecutionError as error:
            return ToolHandlerResult(
                content=json.dumps({
                    "success": False,
                    "code": error.code,
                    "error": str(error),
                }, ensure_ascii=False),
                error_code=error.code,
            )

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(
            name=READ_NOVEL_SOURCE_SLICE,
            description=(
                "读取 Host 为当前 Map 单元绑定的唯一小说来源分片。"
                "不接受 sliceId，不能读取相邻或其他分片。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            display_names={"zh-CN": "读取当前小说分片", "en": "Read source slice"},
        ),
        handler=read_slice,
        policy=ToolPolicy(
            ToolExecutionMode.READ,
            "读取当前小说分片",
            ToolRiskLevel.READ,
        ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=(),
            host_bound_paths=("sourceRevisionId", "sliceId", "ranges"),
        ),
        operation_display_params=lambda state, arguments, call: (
            _map_slice_operation_display_params(state)
        ),
    ),))


@dataclass(frozen=True, slots=True)
class MapChildRunResult:
    child_run_id: str
    payload: Mapping[str, object]


class ScalableMapChildRunner(Protocol):
    async def run(
        self,
        *,
        scope: MapSliceScope,
        pass_id: str,
        dimensions: tuple[str, ...],
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> MapChildRunResult: ...


class PurrAChildJoinCoordinator:
    """Batch Child joins per Root so only one requester transition is active."""

    def __init__(self) -> None:
        self._core = None
        self._lock = asyncio.Lock()
        self._pending = {}
        self._workers = {}

    def bind_agent_core(self, core) -> None:
        if self._core is not None and self._core is not core:
            raise RuntimeError("Child join coordinator is already bound")
        self._core = core

    async def join(self, root_run_id, child_run_id, signal=None):
        if self._core is None:
            raise RuntimeError("Child join coordinator has no active Agent Core")
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending.setdefault(root_run_id, []).append(
                (child_run_id, signal, future)
            )
            worker = self._workers.get(root_run_id)
            if worker is None or worker.done():
                self._workers[root_run_id] = asyncio.create_task(
                    self._run_batches(root_run_id)
                )
        return await future

    async def _run_batches(self, root_run_id):
        try:
            while True:
                # Let sibling durable Units finish their idempotent spawn call
                # before freezing this join batch.
                await asyncio.sleep(0)
                async with self._lock:
                    batch = self._pending.pop(root_run_id, [])
                if not batch:
                    return
                child_ids = tuple(item[0] for item in batch)
                signal = next((item[1] for item in batch if item[1] is not None), None)
                try:
                    aggregation = await self._core.join_agent_runs(
                        root_run_id, child_ids, signal
                    )
                except BaseException as error:
                    for _, _, future in batch:
                        if not future.done():
                            future.set_exception(error)
                else:
                    for _, _, future in batch:
                        if not future.done():
                            future.set_result(aggregation)
        finally:
            async with self._lock:
                if self._workers.get(root_run_id) is asyncio.current_task():
                    self._workers.pop(root_run_id, None)
                if self._pending.get(root_run_id):
                    self._workers[root_run_id] = asyncio.create_task(
                        self._run_batches(root_run_id)
                    )


@dataclass(frozen=True, slots=True)
class _ReusableChildSession:
    agent_id: str
    context_version: int


class PurrAReusableChildCoordinator:
    """Reuse one idle Agent for a stable Host-bound analysis scope."""

    def __init__(self) -> None:
        self._core = None
        self._joins = PurrAChildJoinCoordinator()
        self._state_lock = asyncio.Lock()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._sessions: dict[tuple[str, str], _ReusableChildSession] = {}
        self._receipts = {}

    def bind_agent_core(self, core) -> None:
        if self._core is not None and self._core is not core:
            raise RuntimeError("Reusable Child coordinator is already bound")
        self._core = core
        self._joins.bind_agent_core(core)

    async def run(
        self,
        *,
        root_run_id: str,
        reuse_key: str,
        idempotency_key: str,
        child: ChildAgentSpec,
        signal=None,
    ):
        if self._core is None:
            raise RuntimeError("Reusable Child coordinator has no active Agent Core")
        key = (root_run_id, reuse_key)
        async with self._state_lock:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            completed = self._receipts.get((root_run_id, idempotency_key))
            if completed is not None:
                return completed
            session = self._sessions.get(key)
            if session is None:
                receipt = await self._core.spawn_agents(SpawnAgentsCommand(
                    parent_run_id=root_run_id,
                    idempotency_key=idempotency_key,
                    children=(child,),
                ))
                agent = receipt.items[0].agent
                child_run = receipt.items[0].run
            else:
                receipt = await self._core.continue_agent(ContinueAgentCommand(
                    requester_run_id=root_run_id,
                    idempotency_key=idempotency_key,
                    agent_id=session.agent_id,
                    expected_context_version=session.context_version,
                    message=(
                        child.objective
                        + "\n\nInput:\n"
                        + json.dumps(
                            thaw_json_mapping(child.input_payload),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                    required=child.required,
                    priority=child.priority,
                ))
                agent = receipt.agent
                child_run = receipt.run
            aggregation = await self._joins.join(
                root_run_id, child_run.run_id, signal
            )
            succeeded = (
                not aggregation.pending_run_ids
                and child_run.run_id not in aggregation.required_failures
            )
            self._sessions[key] = _ReusableChildSession(
                agent_id=agent.agent_id,
                context_version=agent.context_version + (1 if succeeded else 0),
            )
            result = (child_run.run_id, aggregation)
            self._receipts[(root_run_id, idempotency_key)] = result
            return result


class PurrAScalableMapChildRunner:
    """Execute one Map through PurrA's public Agent-tree command surface."""

    def __init__(self, db=None, *, model_name: str, submissions=None) -> None:
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            raise ValueError("Map Child runner requires a model name")
        self._core = None
        self._children = PurrAReusableChildCoordinator()
        if submissions is None and db is None:
            raise ValueError("Map Child runner requires a submission store")
        self._submissions = submissions or NovelAnalysisChildSubmissionStore(db)

    def bind_agent_core(self, core) -> None:
        if self._core is not None and self._core is not core:
            raise RuntimeError("Map Child runner is already bound to another Core")
        self._core = core
        self._children.bind_agent_core(core)

    async def run(
        self,
        *,
        scope: MapSliceScope,
        pass_id: str,
        dimensions: tuple[str, ...],
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> MapChildRunResult:
        if self._core is None:
            raise RuntimeError("Map Child runner has no active Agent Core")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        child = ChildAgentSpec(
                name=f"map-{scope.slice_position}"[:64],
                title="分析当前小说分片",
                instruction=(
                    "你只分析 Host 绑定的一个小说分片。必须调用 "
                    f"{READ_NOVEL_SOURCE_SLICE} 读取正文；不要请求其他分片，"
                    "不要输出引文或原文依据。完成后必须调用 "
                    f"{SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT} 提交结果；"
                    "characters 维度按具体人物分别生成 finding；即使原文在同一段"
                    "介绍多人，也不要生成群像或把多个姓名合为一个 subject。"
                    "最终回复不要承载分析数据。提交的 result 只包含 findings，"
                    "其中条目使用 dimension、subject、analysis："
                    '{"findings":[{"dimension":"允许维度",'
                    '"subject":"对象","analysis":"局部分析"}]}。'
                ),
                objective=(
                    f"执行 {pass_id} 分析；允许维度："
                    + ", ".join(dimensions)
                ),
                input_payload={
                    "mapSliceScope": scope.to_mapping(),
                    "unitId": context.unit.id,
                    "attempt": context.unit.attempt,
                },
                capability_grant=AgentCapabilityGrant(
                    can_spawn_agents=False,
                    max_depth=1,
                    max_children_per_call=1,
                    max_agents_per_root=16,
                    max_parallel_runs=1,
                    allowed_tools=(
                        READ_NOVEL_SOURCE_SLICE,
                        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
                    ),
                    allowed_models=(self._model_name,),
                ),
            )
        child_run_id, aggregation = await self._children.run(
            root_run_id=context.run_id,
            reuse_key=f"map-slice:{scope.slice_id}:attempt:{context.unit.attempt}",
            idempotency_key=f"map-child:{operation_id}",
            child=child,
            signal=signal,
        )
        if aggregation.pending_run_ids:
            raise RuntimeError("Map Child remained pending after join")
        if aggregation.required_failures:
            raise_child_run_failure(aggregation, child_run_id, "Map")
        try:
            payload = await self._submissions.load(child_run_id)
        except ValueError as error:
            raise ScalableMapOutputError(
                "Map Child did not submit its structured result"
            ) from error
        return MapChildRunResult(child_run_id=child_run_id, payload=payload)


class ScalableMapUnitExecutor:
    def __init__(self, db, *, child_runner: ScalableMapChildRunner) -> None:
        self._db = db
        self._runner = child_runner
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    def bind_agent_core(self, core) -> None:
        binder = getattr(self._runner, "bind_agent_core", None)
        if not callable(binder):
            raise TypeError("Map Child runner cannot bind an Agent Core")
        binder(core)

    async def execute(self, context, signal=None) -> LongTaskUnitResult:
        raise_if_stopped(signal)
        if str(context.unit.metadata.get("unitKind") or context.unit.metadata.get("kind") or "map") != "map":
            raise ScalableMapExecutionError("scalable Map executor received another Unit kind")
        scope = MapSliceScope.from_manifest(
            context.task.metadata.get("sliceManifest"),
            context.unit.metadata.get("sliceId"),
        )
        pass_id = str(context.unit.metadata.get("passId") or "").strip()
        dimensions = tuple(context.unit.metadata.get("dimensions") or ())
        if not pass_id or not dimensions or any(not isinstance(item, str) for item in dimensions):
            raise ScalableMapExecutionError("Map Unit semantic contract is invalid")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        recovery = await self._artifacts.try_load_execution_payload(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            error_code=getattr(context.unit, "error_code", None),
        )
        recovered_operation_id = None
        if recovery is not None:
            recovered, recovered_operation_id = recovery
            child_run_id = str(recovered.get("childRunId") or "")
            await self._validate_child_ownership(
                context.run_id,
                child_run_id,
                allow_previous_root=recovered_operation_id != operation_id,
            )
            payload = _validate_committed_map_payload(
                recovered,
                pass_id=pass_id,
                slice_id=scope.slice_id,
                child_run_id=child_run_id,
                dimensions=dimensions,
            )
        else:
            result = await self._runner.run(
                scope=scope,
                pass_id=pass_id,
                dimensions=dimensions,
                context=context,
                signal=signal,
            )
            child_run_id = str(result.child_run_id or "").strip()
            await self._validate_child_ownership(context.run_id, child_run_id)
            payload = _validate_map_payload(
                result.payload,
                pass_id=pass_id,
                slice_id=scope.slice_id,
                child_run_id=child_run_id,
                dimensions=dimensions,
            )
        receipt = await self._artifacts.commit(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            operation_id=operation_id,
            run_id=child_run_id,
            payload=dict(payload),
        )
        return LongTaskUnitResult(
            output_ref=receipt.resource_ref,
            artifact_digest=canonical_json_digest(payload),
            validation_receipt={
                "schemaVersion": SCALABLE_MAP_OUTPUT_SCHEMA_VERSION,
                "unitKind": "map",
                "operationId": operation_id,
                "childRunId": child_run_id,
                "sliceId": scope.slice_id,
                "artifactReplayed": receipt.replayed,
                **(
                    {"recoveredOperationId": recovered_operation_id}
                    if recovered_operation_id is not None
                    and recovered_operation_id != operation_id
                    else {}
                ),
            },
            metadata={"artifactId": receipt.artifact_id, "childRunId": child_run_id},
        )

    async def _validate_child_ownership(
        self,
        root_run_id: str,
        child_run_id: str,
        *,
        allow_previous_root: bool = False,
    ) -> None:
        if not child_run_id or child_run_id == root_run_id:
            raise ScalableMapExecutionError("Map model execution requires a Child Run")
        row = await self._db.fetch_one(
            "SELECT root_run_id, parent_run_id FROM ai_agent_runs WHERE id = ?",
            [child_run_id],
        )
        if row is None or not (
            (
                row["root_run_id"] == root_run_id
                and row["parent_run_id"] == root_run_id
            )
            or (
                allow_previous_root
                and row["root_run_id"]
                and row["parent_run_id"] == row["root_run_id"]
            )
        ):
            raise ScalableMapExecutionError("Map Child Run belongs to another Root")

    def classify_failure(self, error: Exception) -> FailureSignal:
        if isinstance(error, ScalableMapOutputError):
            return FailureSignal(
                category=FailureCategory.MODEL_OUTPUT_INVALID,
                code=error.code,
                retryable=True,
            )
        if isinstance(error, ScalableMapExecutionError):
            return FailureSignal(
                category=FailureCategory.BUSINESS_INVARIANT,
                code=error.code,
                retryable=False,
            )
        return FailureSignal(
            category=FailureCategory.TOOL_EXECUTION,
            code=str(getattr(error, "code", "") or type(error).__name__)[:240],
            retryable=True,
        )


def _scope_from_state(state) -> MapSliceScope:
    raw = state.domain.get(SCALABLE_MAP_SCOPE_STATE_KEY)
    if not isinstance(raw, Mapping):
        raise ScalableMapExecutionError("Map slice scope is unavailable")
    return MapSliceScope.from_manifest(
        {"sourceRevisionId": raw.get("sourceRevisionId"),
         "sourceRevisionDigest": raw.get("sourceRevisionDigest"),
         "slices": [{
             "sliceId": raw.get("sliceId"),
             "position": raw.get("slicePosition"),
             "tokenCount": raw.get("tokenCount"),
             "ranges": raw.get("ranges"),
         }]},
        raw.get("sliceId"),
    )


def _map_slice_operation_display_params(state) -> dict[str, object]:
    try:
        scope = _scope_from_state(state)
    except ScalableMapExecutionError:
        return {
            "displayNames": {
                "zh-CN": "读取当前小说分片",
                "en-US": "Read source slice",
            },
        }
    chapter_numbers = _format_chapter_numbers(scope.ranges)
    chapter_range = f"第 {chapter_numbers} 章范围"
    position = scope.slice_position + 1
    return {
        "displayNames": {
            "zh-CN": f"读取第 {position} 个小说分片（{chapter_range}）",
            "en-US": (
                f"Read source slice {position} "
                f"(chapter range {chapter_numbers})"
            ),
        },
        "slicePosition": position,
        "chapterRange": chapter_range,
    }


def _format_chapter_numbers(ranges: tuple[MapSliceRange, ...]) -> str:
    ordinals = sorted({item.section_ordinal + 1 for item in ranges})
    groups: list[tuple[int, int]] = []
    for ordinal in ordinals:
        if groups and ordinal == groups[-1][1] + 1:
            groups[-1] = (groups[-1][0], ordinal)
        else:
            groups.append((ordinal, ordinal))
    values = "、".join(
        str(start) if start == end else f"{start}—{end}"
        for start, end in groups
    )
    return values


async def _scope_for_tool_state(state, tree) -> MapSliceScope:
    if state.domain.get(SCALABLE_MAP_SCOPE_STATE_KEY) is not None:
        return _scope_from_state(state)
    run_id = str(state.run_id or "").strip()
    if not run_id:
        raise ScalableMapExecutionError("Map tool requires a bound Child Run")
    try:
        raw = await inherited_agent_input_payload(tree, run_id, "mapSliceScope")
    except Exception as error:
        raise ScalableMapExecutionError("Map Child Run is unavailable") from error
    if not isinstance(raw, Mapping):
        raise ScalableMapExecutionError("Map Child has no Host-bound slice scope")
    synthetic_state = type("MapState", (), {
        "domain": {SCALABLE_MAP_SCOPE_STATE_KEY: dict(raw)}
    })()
    return _scope_from_state(synthetic_state)


async def inherited_agent_input_payload(tree, run_id: str, key: str):
    """Find immutable Host input on this Run or an earlier Run of the Agent."""

    current_id = str(run_id or "").strip()
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        tree_run = await tree.get_run(current_id)
        raw = tree_run.input_payload.get(key)
        if raw is not None:
            return raw
        current_id = str(tree_run.previous_run_id or "").strip()
    return None


def _validate_map_payload(payload, *, pass_id, slice_id, child_run_id, dimensions):
    if not isinstance(payload, Mapping) or "findings" not in payload:
        raise ScalableMapOutputError("Map model output must contain findings")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ScalableMapOutputError("Map findings must be a list")
    normalized = []
    for item in findings:
        if not isinstance(item, Mapping) or not {"dimension", "subject", "analysis"}.issubset(item):
            raise ScalableMapOutputError("Map finding shape is invalid")
        dimension = str(item.get("dimension") or "")
        subject = str(item.get("subject") or "").strip()
        analysis = str(item.get("analysis") or "").strip()
        if dimension not in dimensions or not subject or not analysis:
            raise ScalableMapOutputError("Map finding is outside its semantic contract")
        normalized.append({"dimension": dimension, "subject": subject, "analysis": analysis})
    return {
        "schemaVersion": SCALABLE_MAP_OUTPUT_SCHEMA_VERSION,
        "kind": "map",
        "passId": pass_id,
        "sliceId": slice_id,
        "childRunId": child_run_id,
        "findings": normalized,
    }


def _validate_committed_map_payload(
    payload, *, pass_id, slice_id, child_run_id, dimensions
):
    if not isinstance(payload, Mapping) or set(payload) != {
        "schemaVersion", "kind", "passId", "sliceId", "childRunId", "findings"
    }:
        raise ScalableMapOutputError("committed Map output shape is invalid")
    if (
        payload.get("schemaVersion") != SCALABLE_MAP_OUTPUT_SCHEMA_VERSION
        or payload.get("kind") != "map"
        or payload.get("passId") != pass_id
        or payload.get("sliceId") != slice_id
        or payload.get("childRunId") != child_run_id
    ):
        raise ScalableMapOutputError("committed Map output identity is invalid")
    return _validate_map_payload(
        {"findings": payload.get("findings")},
        pass_id=pass_id,
        slice_id=slice_id,
        child_run_id=child_run_id,
        dimensions=dimensions,
    )


__all__ = [
    "MapChildRunResult",
    "MapSliceScope",
    "PurrAChildJoinCoordinator",
    "PurrAReusableChildCoordinator",
    "inherited_agent_input_payload",
    "PurrAScalableMapChildRunner",
    "READ_NOVEL_SOURCE_SLICE",
    "SCALABLE_MAP_SCOPE_STATE_KEY",
    "ScalableChildRunError",
    "ScalableMapExecutionError",
    "ScalableMapOutputError",
    "ScalableMapUnitExecutor",
    "SqliteNovelSourceSliceReader",
    "build_scalable_map_source_tool_catalog",
    "raise_child_run_failure",
]
