"""Project-bound context assembly for screenplay Agent requests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_core.context_budget import estimate_json_tokens
from agent_core.errors import AgentCoreError, ContextOverflowError
from agent_core.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudget,
    ContextBudgetClaim,
    ContextBundle,
    TaskContextRequest,
)
from agent_core.ports import CancellationSignal
from domains.screenplay.artifact_projection import (
    SCREENPLAY_ARTIFACT_CONTEXT,
    build_screenplay_artifact_projection,
    build_screenplay_artifact_unavailable_block,
    measure_screenplay_artifact_projection,
    screenplay_artifact_unavailable_demand,
)
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.screenplay.context_packing import pack_screenplay_project_context
from domains.screenplay.source_scope import (
    is_restricted_source_scope,
    parse_source_scope,
    source_scope_summary,
)
from domains.screenplay.query_port import ScreenplayQueryPort
from domains.screenplay.stage_tasks import build_screenplay_stage_planning_facts
from exceptions import NotFoundError


SCREENPLAY_PROJECT_CONTEXT = "screenplay_project"
SCREENPLAY_POLICY_CONTEXT = "screenplay_policy"
_VALID_STAGES = {
    "orientation",
    "brief",
    "structure",
    "scenes",
    "draft",
    "review",
    "completed",
}
_STAGE_GUIDANCE = {
    "orientation": "先建立可靠的素材事实底座，再讨论改编取舍。",
    "brief": "把事实底座转为成片规模、核心冲突与可追溯的逐条改编决策。",
    "structure": "围绕故事梗概、节拍或分集结构提出可审阅方案，不直接写完整剧本。",
    "scenes": "把已确认结构拆为场景目标、冲突、转折与出入场，不越级生成整稿。",
    "draft": "只处理当前指定的场景或段落，并明确承接的已接受上游版本。",
    "review": "检查连贯性、人物弧光与节奏，把诊断和修订建议逐项对应。",
    "completed": "项目已经通过审阅；只回答总结与说明，不再生成新的正式提案。",
}
_STAGE_ARTIFACT_KINDS = {
    "orientation": frozenset({
        "source_analysis_entries",
        "creative_brief_entries",
    }),
    "brief": frozenset({
        "source_analysis_entries",
        "creative_brief_entries",
    }),
    "structure": frozenset({"screenplay_structure_units"}),
    "scenes": frozenset({"scene_list_batches"}),
    "draft": frozenset(),
    "review": frozenset({
        "screenplay_review_entries",
        "screenplay_revision_changes",
    }),
    "completed": frozenset(),
}


class ScreenplayContextProvider:
    def __init__(self, query: ScreenplayQueryPort, *, artifact_continuity=None):
        self._query = query
        self._continuity = artifact_continuity

    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        state = await self._load_state(request, signal)
        return self._build_context_bundle(state, budget)

    async def build_planning_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del budget
        state = await self._load_state(request, signal)
        candidates = (
            await self._continuity.discover(
                namespace=SCREENPLAY_DOMAIN_NAMESPACE,
                owner_id=state.context.project_id,
                session_id=request.session_id,
                allowed_artifact_kinds=_artifact_kinds_for_state(state),
            )
            if self._continuity is not None
            else ()
        )
        return ContextBundle(diagnostics={
            "screenplayProjectId": state.context.project_id,
            "screenplayStage": state.stage,
            "screenplayDocumentCount": len(state.documents),
            "planningContextMode": "lightweight_manifest",
            "requestedStageMatched": True,
            "hostPlanningFacts": _planning_facts(
                state,
                candidates=candidates,
            ),
        })

    async def describe_task_context_demands(
        self,
        request: AgentRunRequest,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> tuple[ContextBudgetClaim, ...]:
        state = await self._load_state(request, signal)
        selection = await self._effective_continuity_selection(
            state,
            request,
            task,
        )
        if _continuity_action(selection) == "ignore":
            return ()
        if self._continuity is None:
            raise RuntimeError("Artifact continuity coordinator is not configured")
        try:
            preview = await self._continuity.preview(
                selection,
                namespace=SCREENPLAY_DOMAIN_NAMESPACE,
                owner_id=state.context.project_id,
                session_id=request.session_id,
                allowed_artifact_kinds=_artifact_kinds_for_state(state),
            )
        except AgentCoreError:
            unavailable_tokens = screenplay_artifact_unavailable_demand()
            return (ContextBudgetClaim(
                name=SCREENPLAY_ARTIFACT_CONTEXT,
                minimum_tokens=unavailable_tokens,
                desired_tokens=unavailable_tokens,
                maximum_tokens=unavailable_tokens,
                priority=90,
            ),)
        if preview is None:
            return ()
        demand = measure_screenplay_artifact_projection(preview)
        return (ContextBudgetClaim(
            name=SCREENPLAY_ARTIFACT_CONTEXT,
            minimum_tokens=demand.minimum_tokens,
            desired_tokens=max(
                demand.minimum_tokens,
                demand.desired_tokens,
            ),
            maximum_tokens=max(
                demand.minimum_tokens,
                demand.desired_tokens,
            ),
            priority=90,
        ),)

    async def build_task_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        state = await self._load_state(request, signal)
        base = self._build_context_bundle(state, budget)
        selection = await self._effective_continuity_selection(
            state,
            request,
            task,
        )
        if _continuity_action(selection) == "ignore":
            return base
        if self._continuity is None:
            raise RuntimeError("Artifact continuity coordinator is not configured")
        try:
            resolution = await self._continuity.resolve(
                selection,
                namespace=SCREENPLAY_DOMAIN_NAMESPACE,
                owner_id=state.context.project_id,
                session_id=request.session_id,
                run_id=task.run_id,
                allowed_artifact_kinds=_artifact_kinds_for_state(state),
            )
        except AgentCoreError as error:
            reason_code = str(
                getattr(error, "code", None)
                or getattr(error, "reason_code", None)
                or "artifact_continuity_unavailable"
            )
            unavailable = build_screenplay_artifact_unavailable_block(
                allocation_tokens=budget.allocation_for(
                    SCREENPLAY_ARTIFACT_CONTEXT
                ),
                reason_code=reason_code,
            )
            return ContextBundle(
                blocks=(*base.blocks, unavailable),
                diagnostics={
                    **dict(base.diagnostics),
                    "artifactContinuity": {
                        "action": _continuity_action(selection),
                        "outcome": "unavailable",
                        "reasonCode": reason_code,
                        "writeClaimAcquired": False,
                    },
                },
            )
        if resolution is None:
            return base
        try:
            projection = build_screenplay_artifact_projection(
                resolution,
                allocation_tokens=budget.allocation_for(
                    SCREENPLAY_ARTIFACT_CONTEXT
                ),
            )
        except ContextOverflowError as error:
            await self._continuity.release_resolution(resolution)
            reason_code = error.reason_code
            unavailable = build_screenplay_artifact_unavailable_block(
                allocation_tokens=budget.allocation_for(
                    SCREENPLAY_ARTIFACT_CONTEXT
                ),
                reason_code=reason_code,
            )
            return ContextBundle(
                blocks=(*base.blocks, unavailable),
                diagnostics={
                    **dict(base.diagnostics),
                    "artifactContinuity": {
                        "action": resolution.action.value,
                        "outcome": "projection_changed",
                        "reasonCode": reason_code,
                        "writeClaimAcquired": False,
                    },
                },
            )
        return ContextBundle(
            blocks=(*base.blocks, projection.block),
            diagnostics={
                **dict(base.diagnostics),
                "artifactContinuity": {
                    "action": resolution.action.value,
                    "artifactId": resolution.record.artifact.id,
                    "workItemId": resolution.record.work_item.id,
                    "artifactRevision": resolution.record.artifact.revision,
                    "includedBatchSequences": (
                        projection.included_batch_sequences
                    ),
                    "omittedBatchCount": len(
                        projection.omitted_batch_sequences
                    ),
                    "omittedMetadataKeyCount": len(
                        projection.omitted_metadata_keys
                    ),
                    "writeClaimAcquired": resolution.write_claim is not None,
                },
            },
        )

    async def _effective_continuity_selection(
        self,
        state: "_ScreenplayContextState",
        request: AgentRunRequest,
        task: TaskContextRequest,
    ) -> Mapping[str, Any] | None:
        del state, request
        return _continuity_selection(task.task_spec.target)

    def _build_context_bundle(
        self,
        state: "_ScreenplayContextState",
        budget: ContextBudget,
    ) -> ContextBundle:
        pack = pack_screenplay_project_context(
            project=_project_payload(state),
            active_document_id=state.active_document_id,
            documents=state.documents,
            stage=state.stage,
            allocation_tokens=budget.allocation_for(
                SCREENPLAY_PROJECT_CONTEXT
            ),
        )
        policy = _build_screenplay_policy(
            stage=state.stage,
            source_book_bound=bool(state.source_book_id),
            source_scope_restricted=is_restricted_source_scope(state.project),
        )
        blocks = (
            ContextBlock(
                name=SCREENPLAY_POLICY_CONTEXT,
                content=policy,
                token_count=estimate_json_tokens(policy),
                untrusted=False,
            ),
            ContextBlock(
                name=SCREENPLAY_PROJECT_CONTEXT,
                content=pack.content,
                token_count=pack.actual_tokens,
                untrusted=True,
            ),
        )
        return ContextBundle(
            blocks=blocks,
            diagnostics={
                "screenplayProjectId": state.context.project_id,
                "screenplayStage": state.stage,
                "screenplayDocumentCount": len(state.documents),
                "screenplayProjectTokens": pack.actual_tokens,
                "screenplayProjectMinimumTokens": pack.minimum_tokens,
                "screenplayProjectDesiredTokens": pack.desired_tokens,
                "screenplayProjectAllocation": budget.allocation_for(
                    SCREENPLAY_PROJECT_CONTEXT
                ),
                "selectedDocumentIds": pack.selected_document_ids,
                "includedDocumentIds": pack.included_document_ids,
                "omittedDocumentIds": pack.omitted_document_ids,
                "requestedStageMatched": True,
                "hostPlanningFacts": _planning_facts(state),
            },
        )

    async def describe_context_demands(
        self,
        request: AgentRunRequest,
        signal: CancellationSignal | None = None,
    ) -> tuple[ContextBudgetClaim, ...]:
        """Measure the current stage pack instead of claiming a fixed cap."""

        state = await self._load_state(request, signal)
        pack = pack_screenplay_project_context(
            project=_project_payload(state),
            active_document_id=state.active_document_id,
            documents=state.documents,
            stage=state.stage,
        )
        return (ContextBudgetClaim(
            name=SCREENPLAY_PROJECT_CONTEXT,
            minimum_tokens=pack.minimum_tokens,
            desired_tokens=pack.desired_tokens,
            maximum_tokens=pack.desired_tokens,
            priority=100,
        ),)

    async def _load_state(
        self,
        request: AgentRunRequest,
        signal: CancellationSignal | None,
    ) -> "_ScreenplayContextState":
        del signal
        context = ScreenplayDomainContext.from_core_context(request.domain_context)
        project = await self._query.get_project(context.project_id)
        if project is None:
            raise NotFoundError("剧本项目不存在")
        project = dict(project)

        source_book_id = _optional_text(project.get("source_book_id"))
        source_scope = parse_source_scope(project.get("source_scope_json"))
        project["source_scope"] = source_scope
        if context.requested_source_book_id != source_book_id:
            raise ValueError("screenplay source book scope does not match project")

        active_document = None
        if context.active_document_id:
            active_document = await self._query.get_document(
                context.project_id,
                context.active_document_id,
            )
            if active_document is None:
                raise ValueError(
                    "active screenplay document does not belong to project"
                )

        documents = list(
            await self._query.list_current_documents(context.project_id)
        )
        if (
            active_document is not None
            and all(
                str(document.get("id") or "")
                != str(active_document.get("id") or "")
                for document in documents
            )
        ):
            documents.append(active_document)
        stage = str(project.get("active_stage") or "orientation").strip()
        if stage not in _VALID_STAGES:
            stage = "orientation"
        if context.requested_stage != stage:
            raise ValueError("screenplay stage scope does not match project")
        draft_scenes: tuple[Mapping[str, Any], ...] = ()
        completed_scene_ids: tuple[str, ...] = ()
        if stage == "draft":
            scene_list = max(
                (
                    document for document in documents
                    if str(document.get("kind") or "") == "scene_list"
                    and str(document.get("status") or "") == "accepted"
                ),
                key=lambda document: int(document.get("version") or 0),
                default=None,
            )
            if scene_list is not None:
                episode_rows = await self._query.list_episode_rows(
                    str(scene_list.get("id") or ""),
                    include_content=True,
                )
                if episode_rows:
                    draft_scenes = tuple(
                        dict(scene)
                        for row in episode_rows
                        for scene in row.get("content_json", {}).get("scenes", [])
                        if isinstance(scene, Mapping)
                    )
                else:
                    scene_content = _json_value(
                        scene_list.get("content_json"),
                        {},
                    )
                    draft_scenes = tuple(
                        dict(scene)
                        for scene in (
                            scene_content.get("scenes", [])
                            if isinstance(scene_content, Mapping)
                            else []
                        )
                        if isinstance(scene, Mapping)
                    )
            draft = max(
                (
                    document for document in documents
                    if str(document.get("kind") or "") == "scene_draft"
                    and str(document.get("status") or "") == "accepted"
                ),
                key=lambda document: int(document.get("version") or 0),
                default=None,
            )
            draft_content = _json_value(
                (draft or {}).get("content_json"),
                {},
            )
            if isinstance(draft_content, Mapping):
                completed_scene_ids = tuple(
                    str(item).strip()
                    for item in draft_content.get("completedSceneIds", [])
                    if str(item).strip()
                )
        if request.session_id is not None:
            if not await self._query.has_open_session(
                request.session_id,
                context.project_id,
            ):
                raise ValueError(
                    "screenplay session does not belong to project"
                )
        return _ScreenplayContextState(
            context=context,
            project=project,
            source_book_id=source_book_id,
            source_scope=source_scope,
            active_document_id=(
                str(active_document["id"]) if active_document else None
            ),
            documents=tuple(documents),
            draft_scenes=draft_scenes,
            completed_scene_ids=completed_scene_ids,
            stage=stage,
        )


def screenplay_context_claims(
    request: AgentRunRequest,
) -> tuple[ContextBudgetClaim, ...]:
    """Legacy static path; screenplay demand is resolved asynchronously."""

    ScreenplayDomainContext.from_core_context(request.domain_context)
    return ()


@dataclass(frozen=True, slots=True)
class _ScreenplayContextState:
    context: ScreenplayDomainContext
    project: Mapping[str, Any]
    source_book_id: str | None
    source_scope: Mapping[str, Any]
    active_document_id: str | None
    documents: tuple[Mapping[str, Any], ...]
    draft_scenes: tuple[Mapping[str, Any], ...]
    completed_scene_ids: tuple[str, ...]
    stage: str


def _artifact_kinds_for_state(
    state: _ScreenplayContextState,
) -> frozenset[str]:
    kinds = _STAGE_ARTIFACT_KINDS[state.stage]
    if state.stage != "orientation":
        return kinds
    return frozenset({
        "source_analysis_entries"
        if state.source_book_id
        else "creative_brief_entries"
    })


def _planning_facts(
    state: _ScreenplayContextState,
    *,
    candidates: tuple[Any, ...] = (),
) -> dict[str, Any]:
    facts = build_screenplay_stage_planning_facts(
        stage=state.stage,
        source_kind=str(state.project.get("source_kind") or "original"),
        screenplay_format=str(state.project.get("format") or ""),
        source_book_bound=bool(state.source_book_id),
        source_scope_restricted=is_restricted_source_scope(state.project),
        documents=state.documents,
        require_deliverable=(
            state.context.task_intent == "stage_deliverable"
        ),
        draft_scene_count=state.context.draft_scene_count,
        draft_scope=state.context.draft_scope,
        bound_draft_scene_ids=state.context.bound_draft_scene_ids,
        draft_scenes=state.draft_scenes,
        completed_scene_ids=state.completed_scene_ids,
    )
    if candidates:
        facts["artifactContinuity"] = {
            "selectionField": "taskSpec.target.artifactContinuity",
            "defaultAction": (
                "ignore"
            ),
            "hostAutoContinuation": False,
            "candidates": [
                {
                    "candidateOrdinal": ordinal,
                    **candidate.planning_view(),
                }
                for ordinal, candidate in enumerate(candidates, start=1)
            ],
        }
    return facts


def _continuity_selection(
    target: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    value = target.get("artifactContinuity")
    return value if isinstance(value, Mapping) else None


def _continuity_action(selection: Mapping[str, Any] | None) -> str:
    return str((selection or {}).get("action") or "ignore").strip()


def _project_payload(state: _ScreenplayContextState) -> dict[str, Any]:
    project = state.project
    return {
        "id": project["id"],
        "title": project["title"],
        "sourceKind": project["source_kind"],
        "sourceBookId": state.source_book_id,
        "sourceScope": source_scope_summary(state.source_scope),
        "format": project["format"],
        "approach": project["approach"],
        "premise": project["premise"],
        "activeStage": state.stage,
        "status": project["status"],
        "deliveryManifest": (
            _json_value(project.get("delivery_manifest_json"), None)
            if state.stage == "completed"
            else None
        ),
    }


def _build_screenplay_policy(
    *,
    stage: str,
    source_book_bound: bool,
    source_scope_restricted: bool,
) -> str:
    source_rule = (
        "项目绑定了来源书籍。只有通过本轮只读素材工具返回的内容才能作为"
        "原作事实；引用时保留工具返回的 sourceType 与 sourceId。"
        if source_book_bound
        else "这是原创剧本项目，不得暗示已经存在未提供的原作事实。"
    )
    range_rule = (
        "项目只改编原作中的限定章节/卷。不得使用范围外章节、章节大纲，"
        "也不得使用无法证明属于该范围的全局人物档案、世界设定或故事背景。"
        if source_scope_restricted
        else "项目的原作叙事范围是整本作品。"
        if source_book_bound
        else ""
    )
    range_line = f"- {range_rule}\n" if range_rule else ""
    proposal_rule = {
        "brief": (
            "正式创作简报必须先调用 beginCreativeBriefArtifact 声明改编决策"
            "数量；书架改编至少 1 条，原创项目为 0。随后按每批最多 8 条调用 "
            "appendCreativeBriefBatch：必须且只能提交一个 brief_content；"
            "formatPlan 不传 targetFormat，由宿主按项目形态注入；书架改编再按"
            "连续 index 提交 adaptation_decision。同一轮可提交多个同名批次，"
            "除新增内容外，每条决策必须用 sourceType + sourceId 锚定已接受"
            "分析证据。原作分析版本、阅读局限和文档谱系均由宿主继承，不要"
            "在参数中重传。remainingItemCount 归零后调用 "
            "finalizeCreativeBriefProposal；"
            "如果发现事实底座有误，可先用原作分析 Artifact 的 begin、append、"
            "finalize 流程提交继承当前分析的修订版；"
            "普通回复只用于澄清与讨论。"
        ),
        "structure": (
            "正式结构版本必须先调用 beginScreenplayStructureArtifact 声明完整"
            "结构单元数；宿主会按项目形态绑定节拍表或分集结构，并返回当前创作"
            "简报中的 expectedDecisionIds。随后按每批最多 20 条调用 "
            "appendScreenplayStructureBatch：structure_unit 使用连续 index、稳定 "
            "id、title 和 summary；decision_coverage 只引用决策 id 并说明结构"
            "落点，不重传改编决策正文。同一轮可提交多个同名批次；所有结构单元"
            "和 expectedDecisionIds 恰好覆盖一次后，调用 "
            "finalizeScreenplayStructureProposal。非删减决策至少映射一个结构"
            "单元，删减决策使用空映射并说明执行方式；原创简报没有决策时无需"
            "伪造 decision_coverage 条目。"
        ),
        "scenes": (
            "正式场景表必须先调用 beginSceneListArtifact 声明场景总数，再按"
            "每批最多 10 场调用 appendSceneListBatch；同一轮可提交多个同名批次，"
            "回执 remainingItemCount "
            "归零后调用 finalizeSceneListProposal；每个场景提供稳定 id 和 "
            "structureUnitIds；所有已接受结构单元必须至少被一个场景承接；"
            "连续剧每场只能归属一个分集；episodeNumber 可省略并由宿主根据"
            "唯一的 structureUnitId 推导，若提供则必须与该分集一致；"
            "场景 id 供逐场正文继承。"
        ),
        "draft": (
            "正式正文必须调用 proposeSceneDraft；sceneText 提交宿主当前批次"
            "绑定的第一场正文，其余场景按顺序放入 additionalScenes，数量必须"
            "等于 hostPlanningFacts.requestedSceneCount。历史整稿、场景 id、"
            "标题和累计完成状态由宿主从已接受版本追加与计算；每场 execution "
            "必须具体说明如何完成场景目标、推进冲突、兑现转折以及场尾连续性"
            "状态，并如实列出未解决事项；不能丢失或改写已接受场景及其执行"
            "记录；角色提示使用 @人物名。"
        ),
        "review": (
            "正式审阅报告先调用 beginScreenplayReviewArtifact，声明 verdict 和"
            "当前问题总数；ready 必须为 0，revise 或 major_rework 至少为 1。"
            "随后按每批最多 8 条调用 appendScreenplayReviewBatch：必须且只能"
            "提交一个 review_summary；问题按连续 index 提交 review_issue，"
            "每项绑定 sceneIds、executionFields 和可复验的 acceptanceCriteria。"
            "如果宿主返回 verificationIssueIds，必须按该顺序用 index 提交全部 "
            "verification_result，不重传上一轮问题 ID、验收标准或解决记录；"
            "未通过或回归项必须继续保留为当前问题，通过项不得保留。"
            "remainingItemCount 归零后调用 finalizeScreenplayReviewProposal；"
            "全部核验通过且没有新问题时才可判定 ready。"
            "只有用户接受审阅报告后，才可调用 beginScreenplayRevisionArtifact。"
            "begin 只提交修订摘要；宿主会从已接受审阅推导必须处理的场景与问题。"
            "随后用 appendScreenplayRevisionBatch 逐场提交新正文及该场 execution，"
            "用 appendScreenplayRevisionResolutionBatch 分批逐项回写审阅问题；"
            "两类回执合计 remainingItemCount 归零后再调用 "
            "finalizeScreenplayRevisionProposal。未修改场景由宿主复用，不能重发"
            "完整剧本或只声称问题已经解决。"
        ),
        "completed": "项目已经完成；不得再调用正式提案工具。",
    }.get(stage, "")
    if stage == "orientation":
        proposal_rule = (
            "本阶段交付物是可审阅的原作范围分析。Agent 应根据可用能力与已获"
            "证据自行规划覆盖、精读和分析步骤；先调用 "
            "beginSourceAnalysisArtifact 声明总述与各类条目数量，再用 "
            "appendSourceAnalysisBatch 分批提交语义分析条目，最后调用 "
            "finalizeSourceAnalysisProposal。章节覆盖、全文精读、抽样和未覆盖"
            "清单由宿主根据本次 Run 的真实读取凭证生成；证据条目仍须使用工具"
            "回执中的 sourceType + sourceId。此阶段不得直接形成创作简报。"
            if source_book_bound
            else "本阶段交付物是可审阅的原创创作简报。应先利用已有项目信息"
            "推进；只有缺少无法安全推断的关键创作决定时，才提出少量高价值"
            "澄清问题。正式版本先调用 beginCreativeBriefArtifact，并将 "
            "expectedDecisionCount 设为 0；再用 appendCreativeBriefBatch 提交"
            "唯一的 brief_content，最后调用 finalizeCreativeBriefProposal。"
        )
    proposal_line = f"- {proposal_rule}\n" if proposal_rule else ""
    return (
        "【PurrTypos 剧本 Agent 工作约定】\n"
        "- 你是协作式剧作顾问与编剧，服务于当前唯一绑定的剧本项目。\n"
        "- 用户做关键创作取舍；你的输出是可审阅提案，不得声称已经保存、接受"
        "或覆盖任何项目文档。\n"
        "- 已接受版本不可被静默改写；提出新版时说明继承内容、变更点和待确认项。\n"
        "- 来源书籍只读，剧本产物独立保存。"
        f"{source_rule}\n"
        f"{range_line}"
        f"- 当前阶段是 {stage}：{_STAGE_GUIDANCE[stage]}\n"
        + proposal_line
        + "- 默认使用清晰、可执行的中文；信息不足时不要补写成既定事实。"
    )


def _json_value(value: object, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
