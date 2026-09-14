"""Verify real GLM Planner chunks through the installed Core and host mapping."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from application.agent_composition import set_agent_composition  # noqa: E402
from application.composition_factory import create_agent_composition  # noqa: E402
from database.connection import DatabaseConnection  # noqa: E402
from routers.ai import _stream_composed_agent  # noqa: E402
from schemas.ai import ChatStreamRequest  # noqa: E402


def configured_glm(settings_db: Path) -> dict:
    uri = settings_db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as source:
        row = source.execute(
            "SELECT value FROM settings WHERE key='ai_model_configs'"
        ).fetchone()
    configs = json.loads(row[0]) if row else []
    config = next(
        item for item in configs
        if item.get("name") == "glm-5.3-flash"
    )
    if config.get("baseUrl") != "https://open.bigmodel.cn/api/paas/v4/":
        raise ValueError("Unexpected GLM endpoint")
    if not str(config.get("apiKey") or "").strip():
        raise ValueError("GLM API key is not configured")
    return config


async def main(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if not output.is_relative_to(Path("/private/tmp").resolve()):
        raise ValueError("Acceptance output must stay under /private/tmp")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new empty acceptance directory")
    output.mkdir(parents=True, exist_ok=True)

    config = configured_glm(args.settings_db)
    db = DatabaseConnection(output / "database")
    await db.init()
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["synthetic-planning-book", "GLM 规划流合成验收"],
    )
    composition = create_agent_composition(db)
    set_agent_composition(composition)
    options = {
        "model": "glm-5.3-flash",
        "model_profile": "zai:glm-5.3-flash",
        "profile_binding": "compatible",
        "thinking": {"type": "enabled"},
        "max_generation_tokens": 2_048,
    }
    body = ChatStreamRequest(
        messages=[{
            "role": "user",
            "content": (
                "先规划再回答：只分析合成句子“小猫把红色球放进蓝色盒子”"
                "的主谓宾和颜色信息，用两句话回答，不调用工具。"
            ),
        }],
        apiKey=config["apiKey"],
        apiProvider="zai",
        baseURL=config["baseUrl"],
        options=options,
        enableAgentTools=False,
        bookId="synthetic-planning-book",
        chatAgentMode="agent",
        planningMode="planned",
        contextWindow="128k",
    )
    provider_options = {**options, "baseURL": config["baseUrl"]}
    started = time.monotonic()
    report = {
        "model": "glm-5.3-flash",
        "provider": "zai",
        "synthetic": True,
        "planningDeltas": [],
        "planningProgress": [],
    }
    try:
        async for frame in _stream_composed_agent(
            body=body,
            api_key=config["apiKey"],
            provider_options=provider_options,
            signal=asyncio.Event(),
        ):
            kind = frame.get("kind")
            payload = frame.get("payload") or {}
            if kind == "planning.delta":
                report["planningDeltas"].append({
                    "elapsedMs": round((time.monotonic() - started) * 1000, 1),
                    "sequence": frame.get("sequence"),
                    "sourceChunkIndex": payload.get("sourceChunkIndex"),
                    "textDelta": payload.get("textDelta"),
                })
            elif kind == "planning.progress":
                report["planningProgress"].append(payload.get("text"))
            if frame.get("done"):
                result = frame.get("runResult") or {}
                report["status"] = result.get("status")
                report["errorCode"] = result.get("errorCode")
                report["runId"] = result.get("runId")
        deltas = report["planningDeltas"]
        report["joinedDelta"] = "".join(
            str(item.get("textDelta") or "") for item in deltas
        )
        report["deltaCount"] = len(deltas)
        report["distinctSourceChunks"] = len({
            item.get("sourceChunkIndex") for item in deltas
        })
        report["passed"] = (
            report.get("status") == "done"
            and report.get("errorCode") is None
            and len(deltas) >= 2
            and report["distinctSourceChunks"] >= 2
            and bool(report["planningProgress"])
            and "{" not in report["joinedDelta"]
            and "\"plan\"" not in report["joinedDelta"]
        )
    except Exception as error:
        report.update(
            status="failed",
            errorCode=str(getattr(error, "code", "") or type(error).__name__),
            passed=False,
        )
    finally:
        report["elapsedSeconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        await composition.shutdown()
        await db.close()
    print(json.dumps({
        "status": report.get("status"),
        "errorCode": report.get("errorCode"),
        "deltaCount": report.get("deltaCount", 0),
        "distinctSourceChunks": report.get("distinctSourceChunks", 0),
        "passed": report.get("passed", False),
        "elapsedSeconds": report["elapsedSeconds"],
    }, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--settings-db",
        type=Path,
        default=Path.home()
        / "Library/Application Support/purrtypos/purrtypos.db",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
