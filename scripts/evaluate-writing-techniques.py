"""Run frozen synthetic cases through native generation tools in an isolated DB.

Credentials are read only from the configured local DB. Outputs, model calls and
review evidence stay in the caller's output directory, never in fixture files.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from application.composition_factory import create_agent_composition
from application.novel_analysis_executor import NovelAnalysisModelCalls
from application.novel_source_service import NovelSourceService
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from domains.writing_technique_prompts import build_generation_prompt, PROMPT_DIGEST, PROMPT_VERSION
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def configured_model(database, model):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        configs = json.loads(connection.execute("SELECT value FROM settings WHERE key='ai_model_configs'").fetchone()[0])
    return next(item for item in configs if item["name"] == model and item.get("apiKey"))


def fixtures():
    root = ROOT / "docs/fixtures/writing-techniques-p0"
    cases = json.loads((root / "cases.json").read_text())
    sources = json.loads((root / "sources.json").read_text())["sources"]
    assert cases["promptReference"]["coreTextSha256"] == PROMPT_DIGEST
    for source in sources:
        assert hashlib.sha256(source["text"].encode()).hexdigest() == source["sha256"]
        assert len(source["text"]) == source["characterCount"]
        for observation in source["observations"]:
            for span in observation["evidence"]:
                assert source["text"][span["start"]:span["end"]] == span["quote"]
    return cases, {source["id"]: source for source in sources}


async def independent_use(config, case, files, output, choices=None, *, reasoning="enabled"):
    """Fresh contexts receive only the task, entry and requested auxiliary files."""
    from openai import AsyncOpenAI
    from infrastructure.persistence.writing.technique_document_parser import entry_metadata
    from domains.writing.prompts import WRITING_TECHNIQUE_USE_POLICY
    from domains.writing.paragraph_validation import requests_single_prose_paragraph, SingleProseParagraphValidator
    metadata = entry_metadata(files["SKILL.md"])
    results = []
    async with AsyncOpenAI(api_key=config["apiKey"], base_url=config["baseUrl"], timeout=120, max_retries=0) as client:
        for task in case["independentUse"]:
            selected_files = None if choices else files
            selected_id = None
            messages = [{"role": "system", "content": WRITING_TECHNIQUE_USE_POLICY + "\n必要时调用 readFile 读取入口所引用的辅助文件。"},
                {"role": "user", "content": json.dumps({"task": task["task"], **({"catalog": [{"id": key, **entry_metadata(value['SKILL.md'])} for key, value in choices.items()], "selection": "先根据名称和用途调用 chooseTechnique 选择，再从返回的入口开始写作。"} if choices else {"technique": metadata, "entry": files["SKILL.md"]})}, ensure_ascii=False)}]
            initial_messages = list(messages)
            reads = [] if choices else [{"path": "SKILL.md", "sha256": hashlib.sha256(files["SKILL.md"].encode()).hexdigest()}]
            calls = []
            rejected_candidates = []
            for _ in range(8):
                response = await client.chat.completions.create(model=config["name"], messages=messages, max_tokens=16384,
                    extra_body={"thinking": {"type": reasoning}}, tools=[{"type": "function", "function": {
                        "name": "readFile", "description": "按相对路径读取当前技法的完整辅助文件。",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
                        *([{"type": "function", "function": {"name": "chooseTechnique", "description": "按目录 ID 选择一个技法并读取入口。", "parameters": {"type": "object", "properties": {"id": {"type": "string", "enum": list(choices)}}, "required": ["id"], "additionalProperties": False}}}] if choices else [])])
                message = response.choices[0].message
                calls.append({"usage": response.usage.model_dump() if response.usage else None, "finishReason": response.choices[0].finish_reason})
                if not message.tool_calls:
                    if response.choices[0].finish_reason != "stop":
                        raise ValueError("Independent writing did not finish normally")
                    validation = SingleProseParagraphValidator().validate(content=message.content, messages=()) if requests_single_prose_paragraph(task["task"]) else None
                    if validation and validation.violation_code:
                        rejected_candidates.append({"text": message.content, "violationCode": validation.violation_code})
                        messages.append({"role": "assistant", "content": message.content,
                                         **({"reasoning_content": message.reasoning_content} if getattr(message, "reasoning_content", None) is not None else {})})
                        messages.append({"role": "system", "content": validation.repair_guidance})
                        continue
                    result = {"taskId": task["id"], "text": message.content, "reads": reads, "calls": calls,
                        "rejectedCandidates": rejected_candidates,
                        "reasoningMode": reasoning, "maxGenerationTokens": 16384,
                        "judgment": "pending_content_review", "initialMessages": initial_messages,
                        "selectedTechnique": selected_id, "selectionCoverage": "executed" if choices and selected_id else "not_covered" if choices else "not_applicable"}
                    results.append(result)
                    break
                assistant_message = {"role": "assistant", "content": message.content,
                    "tool_calls": [call.model_dump() for call in message.tool_calls]}
                reasoning_content = getattr(message, "reasoning_content", None)
                if reasoning_content is not None:
                    assistant_message["reasoning_content"] = reasoning_content
                messages.append(assistant_message)
                for call in message.tool_calls:
                    arguments = json.loads(call.function.arguments)
                    path = arguments.get("path")
                    if call.function.name == 'chooseTechnique' and choices and selected_files is None:
                        selected_id = arguments.get('id')
                        selected_files = choices.get(selected_id)
                        path = 'SKILL.md'
                    content = selected_files.get(path) if selected_files and call.function.name in {'readFile', 'chooseTechnique'} else None
                    if content is not None:
                        reads.append({"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()})
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content if content is not None else "文件不存在"})
            else:
                results.append({"taskId": task["id"], "status": "failed", "reason": "tool_turn_limit", "reads": reads})
                write(output / "independent-use.json", results)
                raise ValueError("Independent writing exhausted its bounded tool/repair rounds")
            write(output / "independent-use.json", results)
    return results


async def run(args):
    cases, sources = fixtures()
    if args.validate_only:
        print(f"Validated {len(sources)} sources and {len(cases['cases'])} cases; no model calls")
        return
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Use a new empty output directory to preserve prior evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    config = configured_model(args.settings_db, args.model)
    runtime = ScreenplayAgentRuntimeRequest(apiKey=config["apiKey"], baseURL=config["baseUrl"], apiProvider=config.get("apiProvider", "openai"),
        options={"model": config["name"], "model_profile": config["presetId"], "profile_binding": "compatible", "max_generation_tokens": 16384,
                 "thinking": {"type": args.reasoning}}, contextWindow=config["contextWindow"])
    write(args.output / "configuration.json", {"model": args.model, "promptVersion": PROMPT_VERSION, "coreDigest": PROMPT_DIGEST,
        "assembledPromptDigest": hashlib.sha256(build_generation_prompt().encode()).hexdigest(), "generationLimit": 16384,
        "reasoningMode": args.reasoning, "scope": "native generation unit and separate direct Provider use; not full analysis pipeline"})
    db = DatabaseConnection(args.output)
    await db.init()
    composition = create_agent_composition(db)
    library = WritingTechniqueService(db)
    models = NovelAnalysisModelCalls(db, composition, runtime)
    report = []
    try:
        for case in cases["cases"]:
            if args.cases and case["id"] not in args.cases.split(","):
                continue
            dest = args.output / case["id"]
            dest.mkdir()
            input_spec = case["generatorInput"]
            source = sources[input_spec["sourceIds"][0]]
            source_service = NovelSourceService(db)
            preview = source_service.preview_external_import(file_name=case["id"] + ".txt", extension=".txt", content=source["text"])
            revision = await source_service.confirm_external_import(title=case["id"], file_name=case["id"] + ".txt", extension=".txt",
                content=source["text"], expected_content_digest=preview["contentDigest"], confirm_single_section=True,
                rights_confirmed=True, model_data_boundary_confirmed=True)
            section = revision["sections"][0]
            task_id = "evaluation_" + uuid.uuid4().hex
            draft = await library.create_draft(operation_id=task_id, storage_scope="analysis_candidate", owner={"taskId": task_id, "sourceRevisionId": revision["id"]})
            observations = [{"contentDigest": item["id"], "cardKind": "来源观察", "title": item["statement"],
                "bodyMarkdown": item["statement"], "supportStatus": item["status"], "sourceObservation": item,
                "evidence": [{"sectionId": section["id"], "excerpt": span["quote"]} for span in item["evidence"]]}
                for item in source["observations"] if item["id"] in input_spec["observationIds"]]
            if getattr(args, "replay_analysis_dir", None):
                from domains.novel_analysis import canonical_digest
                origin = args.replay_analysis_dir
                source_report = json.loads((origin / "report.json").read_text())
                if source_report.get("sourceSha256") != source["sha256"]:
                    raise ValueError("Replay source does not match the selected fixture")
                artifact = json.loads((origin / "analysis-artifact.json").read_text())
                observations = json.loads(json.dumps(artifact["craftCards"]))
                original_sections = {span["sectionId"] for observation in observations for span in observation["evidence"]}
                if len(original_sections) != 1:
                    raise ValueError("Replay currently requires a single frozen section")
                for observation in observations:
                    for span in observation["evidence"]:
                        span["sectionId"] = section["id"]
                    observation.pop("contentDigest", None)
                    observation["contentDigest"] = canonical_digest(observation)
                write(dest / "replay-origin.json", {"runId": source_report["runId"],
                    "artifactSha256": hashlib.sha256((origin / "analysis-artifact.json").read_bytes()).hexdigest(),
                    "sourceSha256": source["sha256"], "bindingRemapOnly": True})
            payload = {"stage": "distill_skill", "sourceKind": source["kind"], "analysisFocus": input_spec["focus"],
                "sourceRevisionId": revision["id"], "sectionIds": [section["id"]], "observations": observations, "techniqueDraft": draft}
            write(dest / "generator-input.json", payload)
            run_ids = []
            async def bind(run_id):
                run_ids.append(run_id)
                write(dest / "runs.json", run_ids)
            context = SimpleNamespace(run_id=task_id, unit=SimpleNamespace(id="skill:draft"),
                task=SimpleNamespace(id=task_id, metadata={"sourceRevisionId": revision["id"], "sectionIds": [section["id"]]}), bind_run=bind)
            item = {"caseId": case["id"], "status": "running", "contentReview": "not_executed"}
            report.append(item)
            write(args.output / "report.json", report)
            print(case["id"], "started", flush=True)
            try:
                run_id, result = await asyncio.wait_for(models.run_json(context=context, instruction=build_generation_prompt(), payload=payload), args.timeout)
                write(dest / "result.json", result)
                outcome = result["techniqueResult"]
                item.update(status=outcome["status"], runId=run_id, structure="pass")
                if outcome["candidate"]:
                    candidate = outcome["candidate"]
                    ref = {"kind": "technique", "id": candidate["techniqueId"], "versionId": candidate["versionId"]}
                    files = library.techniques._version_files(ref)
                    write(dest / "files.json", files)
                    item["multiFile"] = "generated" if len(files) > 1 else "not_covered"
                    if args.independent_use and not case['id'].startswith('E07'):
                        await independent_use(config, case, files, dest, reasoning=args.reasoning)
                else:
                    item["independentUse"] = "not_applicable_no_candidate"
            except Exception as error:
                item.update(status="failed", errorType=type(error).__name__, errorCode=str(getattr(error, "code", "")))
            write(args.output / "report.json", report)
            print(case["id"], item["status"], item.get("errorCode", ""), flush=True)
        pair = [case for case in cases['cases'] if case['id'] in {'E07a', 'E07b'}]
        if args.independent_use and all((args.output / case['id'] / 'files.json').exists() for case in pair):
            choices = {str(index): json.loads((args.output / case['id'] / 'files.json').read_text()) for index, case in enumerate(pair)}
            for index, case in enumerate(pair):
                await independent_use(config, case, choices[str(index)], args.output / case['id'], choices, reasoning=args.reasoning)
    finally:
        for item in report:
            if item['status'] == 'running':
                item['status'] = 'interrupted'
        write(args.output / "report.json", report)
        await composition.shutdown()
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings-db", type=Path, default=Path.home() / "Library/Application Support/purrtypos/purrtypos.db")
    parser.add_argument("--output", type=Path, default=Path("/tmp/writing-techniques-evaluation"))
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--cases", default="")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--reasoning", choices=["enabled", "disabled"], default="enabled")
    parser.add_argument("--independent-use", action="store_true")
    parser.add_argument("--replay-analysis-dir", type=Path,
                        help="Reuse frozen observations from a matching single-section analysis; do not rerun extraction")
    parser.add_argument("--validate-only", action="store_true")
    asyncio.run(run(parser.parse_args()))
