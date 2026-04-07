"""
Human-readable Markdown digests for writing-expert pipeline stages (chat UI).

Shown in the assistant bubble; polish stage intentionally omitted per product spec.
"""

from __future__ import annotations

from typing import Any


def _bullet_list(items: list[Any], max_items: int, prefix: str = "- ") -> list[str]:
    out: list[str] = []
    for x in items[:max_items]:
        t = str(x).strip()
        if t:
            out.append(f"{prefix}{t}")
    return out


# 规划阶段：模型常输出结构化 beat 对象（beatId、position、content 等）
_BEAT_FIELD_LABELS: dict[str, str] = {
    "beatId": "编号",
    "position": "位置",
    "content": "情节",
    "characterFocus": "视角",
    "keyElements": "关键要素",
    "goalLink": "目标关联",
    "constraintCheck": "约束自检",
}

_BEAT_FIELD_ORDER: tuple[str, ...] = (
    "beatId",
    "position",
    "characterFocus",
    "content",
    "keyElements",
    "goalLink",
    "constraintCheck",
)


def _format_beat_digest_lines(b: Any) -> list[str]:
    """将单条节拍渲染为 Markdown 列表块（避免 str(dict) 原样展示）。"""
    if isinstance(b, str):
        t = b.strip()
        return [f"- {t}"] if t else []
    if not isinstance(b, dict):
        t = str(b).strip()
        return [f"- {t}"] if t else []

    lines: list[str] = []
    bid = str(b.get("beatId") or "").strip()
    pos = str(b.get("position") or "").strip()
    title_bits = [x for x in (bid, pos) if x]
    if title_bits:
        lines.append(f"- **{' · '.join(title_bits)}**")
    else:
        lines.append("- **节拍**")

    def append_field(label: str, text: str, multiline: bool) -> None:
        if not text:
            return
        if multiline:
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            if not parts:
                return
            first, rest = parts[0], parts[1:13]
            lines.append(f"  - **{label}**　{first}")
            for para in rest:
                lines.append(f"    - {para}")
            if len(parts) > 13:
                lines.append("    - …")
        else:
            if len(text) > 1200:
                text = text[:1200] + "…"
            lines.append(f"  - **{label}**　{text}")

    for key in _BEAT_FIELD_ORDER:
        if key in ("beatId", "position"):
            continue
        if key not in b:
            continue
        val = b[key]
        label = _BEAT_FIELD_LABELS.get(key, key)
        if isinstance(val, list):
            items = [str(x).strip() for x in val if str(x).strip()]
            if not items:
                continue
            joined = "；".join(items)
            if len(joined) > 800:
                joined = joined[:800] + "…"
            lines.append(f"  - **{label}**　{joined}")
        else:
            text = str(val).strip()
            append_field(label, text, multiline=key == "content" and "\n" in text)

    order_set = frozenset(_BEAT_FIELD_ORDER)
    for key in sorted(str(k) for k in b.keys()):
        if key in order_set:
            continue
        val = b.get(key)
        label = _BEAT_FIELD_LABELS.get(key, key)
        if isinstance(val, list):
            items = [str(x).strip() for x in val if str(x).strip()]
            if not items:
                continue
            joined = "；".join(items)
            if len(joined) > 600:
                joined = joined[:600] + "…"
            lines.append(f"  - **{label}**　{joined}")
        else:
            text = str(val).strip() if val is not None else ""
            append_field(label, text, multiline="\n" in text)

    return lines


def format_analyze_digest(report: dict | None) -> str:
    if not report or not isinstance(report, dict):
        return "### 分析结果\n\n（无法展示：分析数据为空）"
    lines = ["### 分析结果", ""]
    summary = str(report.get("summary") or "").strip()
    if summary:
        lines.append(f"**摘要**　{summary}")
        lines.append("")
    for label, key in (
        ("目标", "goals"),
        ("约束", "constraints"),
        ("风险", "risks"),
    ):
        raw = report.get(key)
        if isinstance(raw, list) and raw:
            lines.append(f"**{label}**")
            lines.extend(_bullet_list(raw, 16))
            lines.append("")
    evidence = report.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.append("**证据摘录**")
        for ev in evidence[:10]:
            if not isinstance(ev, dict):
                continue
            src = str(ev.get("source") or "").strip()
            snip = str(ev.get("snippet") or "").strip()
            if len(snip) > 200:
                snip = snip[:200] + "…"
            if src or snip:
                lines.append(f"- {src or '（来源）'}：{snip or '…'}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def format_plan_digest(blueprint: dict | None) -> str:
    if not blueprint or not isinstance(blueprint, dict):
        return "### 规划结果\n\n（无法展示：蓝图数据为空）"
    lines = ["### 规划结果", ""]
    cg = str(blueprint.get("chapterGoal") or "").strip()
    if cg:
        lines.append(f"**本章目标**　{cg}")
        lines.append("")
    tone = str(blueprint.get("tone") or "").strip()
    if tone:
        lines.append(f"**语气/调性**　{tone}")
        lines.append("")
    beats = blueprint.get("beats")
    if isinstance(beats, list) and beats:
        lines.append("**节拍 / 关键情节**")
        for beat in beats[:24]:
            lines.extend(_format_beat_digest_lines(beat))
        lines.append("")
    cons = blueprint.get("constraints")
    if isinstance(cons, list) and cons:
        lines.append("**落笔约束**")
        lines.extend(_bullet_list(cons, 16))
        lines.append("")
    mats = blueprint.get("requiredMaterials")
    if isinstance(mats, list) and mats:
        lines.append("**所需素材**")
        for m in mats[:20]:
            if not isinstance(m, dict):
                continue
            t = str(m.get("type") or "").strip()
            ref = str(m.get("ref") or "").strip()
            note = str(m.get("note") or "").strip()
            if len(note) > 160:
                note = note[:160] + "…"
            bits = " / ".join(x for x in (t, ref, note) if x)
            if bits:
                lines.append(f"- {bits}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def format_draft_thoughts_digest(draft: dict | None) -> str:
    """撰稿阶段：展示标题与 notes（思路），不重复贴全文正文。"""
    if not draft or not isinstance(draft, dict):
        return "### 撰稿思路\n\n（无法展示：撰稿数据为空）"
    lines = ["### 撰稿思路", ""]
    title = str(draft.get("title") or "").strip()
    if title:
        lines.append(f"**拟定标题**　{title}")
        lines.append("")
    notes = draft.get("notes")
    if isinstance(notes, list) and notes:
        lines.append("**写作取舍与说明**")
        lines.extend(_bullet_list(notes, 16))
        lines.append("")
    content = str(draft.get("content") or "").strip()
    if content:
        wc = len(content.replace("\n", "").replace("\r", ""))
        lines.append(f"*（初稿已生成，约 **{wc}** 字；完整正文见下方主稿专家汇总。）*")
    elif not (title or (isinstance(notes, list) and notes)):
        lines.append("（见主稿专家汇总。）")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


_SEVERITY_ORDER = ("high", "medium", "low")


def format_review_digest(issues: list[dict] | None) -> str:
    if not issues:
        return "### 审校总结\n\n（当前文本未发现问题，或无可审校正文。）"
    lines = ["### 审校总结", "", f"共检出 **{len(issues)}** 条意见（以下为摘要）。", ""]
    by_type: dict[str, int] = {}
    for x in issues:
        if not isinstance(x, dict):
            continue
        it = str(x.get("issueType") or "general").strip() or "general"
        by_type[it] = by_type.get(it, 0) + 1
    if by_type:
        parts = [f"{k}：{v}" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])]
        lines.append("**分类统计**　" + "；".join(parts))
        lines.append("")
    def _sev_key(s: str) -> int:
        sl = (s or "").lower()
        try:
            return _SEVERITY_ORDER.index(sl)
        except ValueError:
            return 3

    sorted_issues = sorted(
        [x for x in issues if isinstance(x, dict)],
        key=lambda x: (_sev_key(str(x.get("severity") or "medium")), str(x.get("span") or "")),
    )
    lines.append("**主要问题**")
    shown = 0
    for x in sorted_issues:
        if shown >= 18:
            lines.append(f"- … 另有 {len(sorted_issues) - shown} 条，详见主稿专家说明中的结构化列表。")
            break
        span = str(x.get("span") or "").strip()
        if len(span) > 80:
            span = span[:80] + "…"
        sev = str(x.get("severity") or "medium").strip()
        itype = str(x.get("issueType") or "").strip()
        sug = str(x.get("suggestion") or "").strip()
        if len(sug) > 220:
            sug = sug[:220] + "…"
        head = f"[{sev}/{itype}]" if itype else f"[{sev}]"
        line = f"- {head} {span or '（位置略）'}"
        if sug:
            line += f" — *建议：* {sug}"
        lines.append(line)
        shown += 1
    return "\n".join(lines).strip()
