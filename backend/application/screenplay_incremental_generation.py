"""Checkpointed screenplay generation above PurrA model/tool Runs.

Each model Run owns one bounded fragment. The application persists a fragment
before requesting the next one, so a provider limit, process restart, or unit
retry resumes at the first uncommitted fragment instead of regenerating the
whole deliverable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from purra.contracts import ReasoningMode

from application.output_budget_policies import (
    SCREENPLAY_FRAGMENT_OUTPUT_POLICY,
    SCREENPLAY_METADATA_OUTPUT_POLICY,
    SCREENPLAY_SCENE_OUTPUT_POLICY,
)


class ScreenplayIncrementalGeneration:
    def __init__(
        self,
        *,
        context,
        outputs,
        tool_calls,
        domain_context_factory,
    ) -> None:
        self._context = context
        self._outputs = outputs
        self._tool_calls = tool_calls
        self._domain_context_factory = domain_context_factory

    async def generate_episode(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal,
        episode_number: int,
        base_revision_id: str | None,
        scene_ids: Sequence[str],
        scene_list_id: str,
        validate_scene,
        validate_metadata,
    ) -> dict[str, Any]:
        writing_context = await self._context.episode_writing_context(
            str(task["projectId"]),
            episode_number,
            draft_revision_id=base_revision_id,
        )
        scene_plans = dict(writing_context["scenePlans"])
        if tuple(scene_plans) != tuple(scene_ids):
            raise RuntimeError("screenplay_scene_plan_manifest_mismatch")
        current_draft_scenes = dict(
            writing_context.get("currentDraftScenes") or {}
        )
        prior_episode = _prior_episode_continuity(
            task.get("units") or (),
            episode_number,
        ) or writing_context.get("previousEpisodeContinuity")
        scenes: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        for position, scene_id in enumerate(scene_ids, start=1):
            checkpoint = await self._checkpoint_candidate(
                task=task,
                unit=unit,
                checkpoint_id=f"scene-{position}",
                runtime=runtime,
                signal=signal,
                prompt=str(unit["input"].get("instruction") or "创作剧本场景"),
                system_instruction=_scene_tool_instruction(
                    episode_number,
                    scene_id,
                ),
                user_payload={
                    "task": "create_screenplay_scene",
                    "episodeNumber": episode_number,
                    "sceneId": scene_id,
                    "scenePosition": position,
                    "sceneCount": len(scene_ids),
                    "instruction": unit["input"].get("instruction"),
                    "constraints": unit["input"].get("constraints") or [],
                    "preserve": unit["input"].get("preserve") or [],
                    "baseRevisionId": base_revision_id,
                    "scenePlan": scene_plans[scene_id],
                    "currentDraftScene": current_draft_scenes.get(scene_id),
                    "revisionIssues": [
                        {
                            **dict(issue),
                            "directlyReferencesCurrentScene": (
                                scene_id in issue.get("relatedSceneIds", ())
                            ),
                        }
                        for issue in writing_context.get("reviewIssues") or ()
                    ],
                    "reviewRevisionId": writing_context.get(
                        "reviewRevisionId"
                    ),
                    "acceptedGuidance": writing_context.get(
                        "acceptedGuidance"
                    ),
                    "previousEpisodeContinuity": prior_episode,
                    "completedSceneSummaries": [
                        {
                            "sceneId": scene["sceneId"],
                            "processSummary": scene["processSummary"],
                        }
                        for scene in scenes
                    ],
                    "previousSceneTail": (
                        str(scenes[-1]["sceneText"])[-1_200:] if scenes else ""
                    ),
                },
                expected_part_type="scene",
                expected_part_key=scene_id,
                output_policy=SCREENPLAY_SCENE_OUTPUT_POLICY,
                reasoning_mode=ReasoningMode.DISABLED,
                host_candidate_template=_host_scene_candidate_template(
                    scene_id,
                    scene_plans[scene_id],
                ),
                validate_candidate=lambda candidate, expected=scene_id: (
                    validate_scene(candidate, expected)
                ),
            )
            scene = dict(checkpoint["payload"])
            if scene["sceneId"] != scene_id:
                raise RuntimeError("screenplay_scene_checkpoint_mismatch")
            scenes.append(scene)
            checkpoints.append(checkpoint)

        metadata = await self._checkpoint_candidate(
            task=task,
            unit=unit,
            checkpoint_id="episode-metadata",
            runtime=runtime,
            signal=signal,
            prompt=f"整理第 {episode_number} 集标题和连续性摘要",
            system_instruction=_episode_metadata_tool_instruction(episode_number),
            user_payload={
                "task": "finalize_screenplay_episode_metadata",
                "episodeNumber": episode_number,
                "sceneSummaries": [
                    {
                        "sceneId": scene["sceneId"],
                        "processSummary": scene["processSummary"],
                    }
                    for scene in scenes
                ],
                "finalSceneTail": str(scenes[-1]["sceneText"])[-1_200:],
            },
            expected_part_type="episode_metadata",
            expected_part_key=str(episode_number),
            output_policy=SCREENPLAY_METADATA_OUTPUT_POLICY,
            reasoning_mode=ReasoningMode.DISABLED,
            validate_candidate=lambda candidate: validate_metadata(
                candidate,
                episode_number,
            ),
        )
        checkpoints.append(metadata)
        scene_texts = [{
            "sceneId": scene["sceneId"],
            "contentText": scene["sceneText"],
        } for scene in scenes]
        content_text = "\n\n".join(item["contentText"] for item in scene_texts)
        episode_metadata = dict(metadata["payload"])
        return {
            "runId": metadata["runId"],
            "sourceRunIds": [item["runId"] for item in checkpoints],
            "artifactId": metadata["artifactId"],
            "artifactIds": [item["artifactId"] for item in checkpoints],
            "sceneListId": scene_list_id,
            "episodeDraft": {
                "episodeNumber": episode_number,
                "title": episode_metadata["title"],
                "sceneIds": list(scene_ids),
                "sceneTexts": scene_texts,
                "sceneExecutions": [{
                    "sceneId": scene["sceneId"],
                    "status": "completed",
                    "processSummary": scene["processSummary"],
                } for scene in scenes],
                "contentText": content_text,
                "continuitySummary": episode_metadata["continuitySummary"],
            },
        }

    async def generate_review(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal,
        reviewed_draft_id: str | None,
        draft_scene_ids,
        validate_fragment,
        aggregate_verdict,
        document_kind: str,
    ) -> dict[str, Any]:
        if not reviewed_draft_id:
            raise ValueError("review requires an accepted screenplay draft")
        available = await self._context.available_episode_numbers(
            str(task["projectId"]),
            draft_revision_id=reviewed_draft_id,
        )
        episode_numbers = tuple(available["draft"])
        if not episode_numbers:
            raise ValueError("review requires at least one draft episode")
        checkpoints: list[dict[str, Any]] = []
        for episode_number in episode_numbers:
            episode = await self._context.episode_context(
                str(task["projectId"]),
                episode_number,
                draft_revision_id=reviewed_draft_id,
            )
            scene_ids = draft_scene_ids(episode.get("currentDraft"))
            checkpoint = await self._checkpoint_candidate(
                task=task,
                unit=unit,
                checkpoint_id=f"review-episode-{episode_number}",
                runtime=runtime,
                signal=signal,
                prompt=str(unit["input"].get("instruction") or "审阅剧本"),
                system_instruction=_review_fragment_tool_instruction(
                    episode_number,
                    scene_ids,
                ),
                user_payload={
                    "task": "review_screenplay_episode",
                    "episodeNumber": episode_number,
                    "reviewedDraftId": reviewed_draft_id,
                    "instruction": unit["input"].get("instruction"),
                    "constraints": unit["input"].get("constraints") or [],
                    "preserve": unit["input"].get("preserve") or [],
                },
                expected_part_type="review_episode",
                expected_part_key=str(episode_number),
                output_policy=SCREENPLAY_FRAGMENT_OUTPUT_POLICY,
                validate_candidate=lambda candidate, number=episode_number, ids=scene_ids: (
                    validate_fragment(candidate, number, ids, reviewed_draft_id)
                ),
            )
            checkpoints.append(checkpoint)
        issues = [
            dict(issue)
            for checkpoint in checkpoints
            for issue in checkpoint["payload"]["contentJson"]["issues"]
        ]
        verdict = aggregate_verdict(
            str(checkpoint["payload"]["contentJson"]["verdict"])
            for checkpoint in checkpoints
        )
        content_text = "\n\n".join(
            f"## 第 {number} 集\n\n{checkpoint['contentText']}"
            for number, checkpoint in zip(episode_numbers, checkpoints)
        )
        content_json = {
            "schemaVersion": 1,
            "documentKind": document_kind,
            "verdict": verdict,
            "issues": issues,
            "issueCount": len(issues),
            "criticalIssueCount": sum(
                issue["severity"] == "critical" for issue in issues
            ),
            "reviewedDraftId": reviewed_draft_id,
            "reviewedEpisodes": list(episode_numbers),
        }
        return {
            "title": "剧本审阅报告",
            "executionSummary": (
                f"已按 {len(episode_numbers)} 集分别审阅并汇总问题，"
                "各集结果已独立保存。"
            ),
            "contentText": content_text,
            "contentJson": content_json,
            "runId": checkpoints[-1]["runId"],
            "sourceRunIds": [item["runId"] for item in checkpoints],
            "artifactId": checkpoints[-1]["artifactId"],
            "artifactIds": [item["artifactId"] for item in checkpoints],
        }

    async def generate_scene_list(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal,
        structure_id: str | None,
        episode_numbers: Sequence[int],
        validate_fragment,
        finalize_document,
    ) -> dict[str, Any]:
        if not structure_id or not episode_numbers:
            raise ValueError("scene list requires an accepted episode structure")
        checkpoints: list[dict[str, Any]] = []
        for episode_number in episode_numbers:
            checkpoint = await self._checkpoint_candidate(
                task=task,
                unit=unit,
                checkpoint_id=f"scene-list-episode-{episode_number}",
                runtime=runtime,
                signal=signal,
                prompt=str(unit["input"].get("instruction") or "生成场景表"),
                system_instruction=_scene_list_fragment_tool_instruction(
                    episode_number
                ),
                user_payload={
                    "task": "create_scene_list_episode",
                    "episodeNumber": episode_number,
                    "structureId": structure_id,
                    "instruction": unit["input"].get("instruction"),
                    "constraints": unit["input"].get("constraints") or [],
                    "preserve": unit["input"].get("preserve") or [],
                },
                expected_part_type="scene_list_episode",
                expected_part_key=str(episode_number),
                output_policy=SCREENPLAY_FRAGMENT_OUTPUT_POLICY,
                validate_candidate=lambda candidate, number=episode_number: (
                    validate_fragment(candidate, number)
                ),
            )
            checkpoints.append(checkpoint)
        scenes = [
            dict(scene)
            for checkpoint in checkpoints
            for scene in checkpoint["payload"]["contentJson"]["scenes"]
        ]
        content_text = "\n\n".join(
            checkpoint["contentText"] for checkpoint in checkpoints
        )
        normalized = finalize_document(
            scenes,
            content_text,
            structure_id,
            episode_numbers,
        )
        return {
            **normalized,
            "runId": checkpoints[-1]["runId"],
            "sourceRunIds": [item["runId"] for item in checkpoints],
            "artifactId": checkpoints[-1]["artifactId"],
            "artifactIds": [item["artifactId"] for item in checkpoints],
        }

    async def _checkpoint_candidate(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        checkpoint_id: str,
        runtime,
        signal,
        prompt: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        expected_part_type: str,
        expected_part_key: str,
        output_policy,
        validate_candidate,
        host_candidate_template=None,
        reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT,
    ) -> dict[str, Any]:
        unit_id = f"{unit['id']}:{checkpoint_id}"
        cached = await self._outputs.load_unit(str(task["id"]), unit_id)
        if cached is not None:
            return dict(cached[1])
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=prompt,
            system_instruction=system_instruction,
            user_payload=dict(user_payload),
            domain_context=await self._domain_context_factory(
                task,
                unit,
                expected_part_type=expected_part_type,
                expected_part_key=expected_part_key,
                runtime=runtime,
                unit_id=unit_id,
            ),
            conversation_turn_id=str(task["turnId"]),
            output_policy=output_policy,
            work_units=1,
            reasoning_mode=reasoning_mode,
            host_candidate_template=host_candidate_template,
            validate_candidate=validate_candidate,
            signal=signal,
        )
        checkpoint = {
            "runId": result.run_id,
            "artifactId": str(result.candidate["artifactId"]),
            "payload": dict(result.candidate["payload"]),
            "contentText": str(result.candidate.get("contentText") or ""),
        }
        await self._outputs.put(
            task_id=str(task["id"]),
            unit_id=unit_id,
            output=checkpoint,
        )
        return checkpoint


def _scene_tool_instruction(episode_number: int, scene_id: str) -> str:
    return f"""你是专业剧本编剧，只创作第 {episode_number} 集中的场景 {scene_id}。
当前任务、场景计划、对应旧稿、审阅问题和已采纳创作依据均已由宿主完整提供。不得另行检索、扩大到其他场景或重新规划任务。
revisionIssues 中的条目都是本集修订约束；directlyReferencesCurrentScene 只表示审阅报告是否直接点名当前场景，不能据此忽略本集统一格式、节奏或连续性要求。

最终回复只输出当前场景的完整可拍摄剧本文本。不得输出 JSON、Markdown 代码块、过程说明、确认语、整集或其他场景。场景身份和写入由宿主负责。"""


def _host_scene_candidate_template(
    scene_id: str,
    scene_plan: Mapping[str, Any],
) -> dict[str, Any]:
    summary_parts = [
        str(scene_plan.get(key) or "").strip()
        for key in ("objective", "conflict", "turn")
    ]
    summary = "；".join(part for part in summary_parts if part)
    summary = " ".join(summary.replace("{", "").replace("}", "").split())
    if not summary:
        summary = "按已采纳场景计划推进人物目标、冲突与转折。"
    return {
        "sceneId": scene_id,
        "processSummary": f"场景 {scene_id} 推演：{summary}"[:600],
    }


def _prior_episode_continuity(
    units: Sequence[Mapping[str, Any]],
    episode_number: int,
) -> dict[str, Any] | None:
    candidates = []
    for unit in units:
        if unit.get("status") != "completed":
            continue
        output = unit.get("output")
        draft = (
            output.get("episodeDraft")
            if isinstance(output, Mapping)
            else None
        )
        if not isinstance(draft, Mapping):
            continue
        number = int(draft.get("episodeNumber") or 0)
        if number <= 0 or number >= episode_number:
            continue
        scene_texts = draft.get("sceneTexts")
        final_text = ""
        if isinstance(scene_texts, list) and scene_texts:
            final_scene = scene_texts[-1]
            if isinstance(final_scene, Mapping):
                final_text = str(final_scene.get("contentText") or "")
        candidates.append({
            "episodeNumber": number,
            "title": str(draft.get("title") or ""),
            "continuitySummary": str(
                draft.get("continuitySummary") or ""
            )[:2_000],
            "finalSceneTail": final_text[-1_200:],
        })
    return candidates[-1] if candidates else None


def _episode_metadata_tool_instruction(episode_number: int) -> str:
    return f"""你只负责整理第 {episode_number} 集的短元数据，不生成或复述剧本正文。
根据宿主提供的场景摘要，完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须严格为：
{{"episodeNumber":{episode_number},"title":"简洁集标题","executionSummary":"2 至 4 句公开创作说明","continuitySummary":"供下一集续写的简要连续性摘要"}}
写入成功后只回复一句简短确认。"""


def _review_fragment_tool_instruction(
    episode_number: int,
    scene_ids: Sequence[str],
) -> str:
    return f"""你是剧本审阅 Agent，只审阅第 {episode_number} 集，不生成全剧报告。
按需调用工具读取指定版本的该集正文、场景计划和必要上下文。完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须为：
{{"title":"第 {episode_number} 集审阅","executionSummary":"2 至 4 句公开审阅说明","contentText":"当前集的 Markdown 审阅意见","contentJson":{{"verdict":"ready|revise|major_rework","issues":[{{"id":"集内唯一 ID","severity":"critical|major|minor","description":"具体问题与修改方向","sceneIds":["场景ID"]}}]}}}}
问题只能引用这些场景 ID：{list(scene_ids)}。没有问题时 issues=[] 且 verdict=ready。不得输出其他集或全剧正文。"""


def _scene_list_fragment_tool_instruction(episode_number: int) -> str:
    return f"""你是剧本场景规划 Agent，只规划已采纳结构中的第 {episode_number} 集。
按需调用工具读取结构、创作简报和来源证据。完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须为：
{{"title":"第 {episode_number} 集场景表","executionSummary":"2 至 4 句公开规划说明","contentText":"当前集场景表的 Markdown 文档","contentJson":{{"scenes":[{{"id":"全局唯一场景 ID","episodeNumber":{episode_number},"heading":"内外景·地点·时间","objective":"目标","conflict":"冲突","turn":"转折","synopsis":"场景梗概"}}]}}}}
只提交当前集，场景顺序必须可直接用于后续剧本创作。"""


__all__ = ["ScreenplayIncrementalGeneration"]
