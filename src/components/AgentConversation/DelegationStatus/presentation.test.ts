import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSubAgentConversationMessages,
  collapseSubAgentDelegations,
} from "./presentation.ts";

test("sub-Agent conversation uses the delegated prompt as the user message", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{ runId: "run-1", prompt: "检查人物动机是否连续。", finalResponse: "", status: "done" }],
    messagesByRunId: new Map([["run-1", { role: "assistant", content: "已完成检查。" }]]),
  });

  assert.deepEqual(messages.map((message) => message.role), ["user", "assistant"]);
  assert.equal(messages[0].content, "检查人物动机是否连续。");
  assert.equal(messages[1].content, "已完成检查。");
});

test("persisted private child result becomes readable conversation text", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{ runId: "run-1", prompt: "归并分析。", finalResponse: '{"findings":[{"subject":"林月"}]}', status: "done" }],
    messagesByRunId: new Map([["run-1", { role: "assistant", content: "" }]]),
  });

  assert.equal(
    messages[1].content,
    "## 分析结果\n\n林月",
  );
});

test("structured findings are projected without exposing raw JSON", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{
      runId: "run-1",
      prompt: "分析人物。",
      finalResponse: JSON.stringify({
        findings: [{ subject: "林月", analysis: "人物动机连续。" }],
      }),
      status: "done",
    }],
  });

  assert.equal(
    messages[1].content,
    "## 分析结果\n\n### 林月\n\n人物动机连续。",
  );
  assert.doesNotMatch(messages[1].content, /[{}]/);
});

test("completed structured output keeps summary, materials, and techniques", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{
      runId: "run-1",
      prompt: "形成整书分析。",
      finalResponse: `已完成分析。\n\n\`\`\`json\n${JSON.stringify({
        summaryMarkdown: "整书总结。",
        facts: [{ subjectKey: "林月", value: { profile_md: "人物资料。" } }],
        craftCards: [{ title: "章末钩子", bodyMarkdown: "技法内容。" }],
      })}\n\`\`\``,
      status: "done",
    }],
  });

  assert.match(messages[1].content, /整书总结/);
  assert.match(messages[1].content, /林月/);
  assert.match(messages[1].content, /人物资料/);
  assert.match(messages[1].content, /章末钩子/);
  assert.match(messages[1].content, /技法内容/);
  assert.doesNotMatch(messages[1].content, /[{}]/);
});

test("completed private result clears the generic empty-response interruption", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{ runId: "run-1", prompt: "分析。", finalResponse: "", status: "done" }],
    messagesByRunId: new Map([["run-1", {
      role: "assistant",
      content: "",
      error: "本轮处理已结束，但模型没有生成可展示的答复。请重试或更换模型。",
      isError: false,
      termination: "受控中断",
    }]]),
  });

  assert.equal(messages[1].content, "已完成处理，结果已交付主 Agent。");
  assert.equal(messages[1].error, undefined);
  assert.equal(messages[1].termination, undefined);
});

test("failed child output uses the ordinary assistant error state", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{ runId: "run-1", prompt: "审核分析。", finalResponse: "", status: "failed" }],
    error: "审核失败",
  });

  assert.equal(messages[1].isError, true);
  assert.equal(messages[1].error, "审核失败");
});

test("continued Agent turns remain in conversation order", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [
      { runId: "run-1", prompt: "先分析人物。", finalResponse: "人物结果", status: "done" },
      { runId: "run-2", prompt: "继续分析设定。", finalResponse: "设定结果", status: "done" },
    ],
  });

  assert.deepEqual(
    messages.map((message) => message.content),
    ["先分析人物。", "人物结果", "继续分析设定。", "设定结果"],
  );
});

test("malformed structured child output never exposes raw protocol JSON", () => {
  const messages = buildSubAgentConversationMessages({
    turns: [{
      runId: "run-1",
      prompt: "形成整书分析。",
      finalResponse: '{"summaryMarkdown":"已形成整书总结。","facts":[',
      status: "done",
    }],
  });

  assert.equal(messages[1].content, "已形成整书总结。");
  assert.doesNotMatch(messages[1].content, /[{}]/);
});

test("fresh retry runs collapse into one logical Unit row", () => {
  const rows = collapseSubAgentDelegations([
    {
      delegationId: "run:first",
      runId: "first",
      agentId: "agent-first",
      agentName: "synthesis-first",
      agentTitle: "形成整书分析总结",
      objective: "形成总结",
      unitId: "synthesis:whole-work",
      attempt: 1,
      startedAt: "2026-09-16T16:08:23.794020+08:00",
      status: "failed",
      required: true,
      priority: 0,
    },
    {
      delegationId: "run:retry",
      runId: "retry",
      agentId: "agent-retry",
      agentName: "synthesis-retry",
      agentTitle: "形成整书分析总结",
      objective: "形成总结",
      unitId: "synthesis:whole-work",
      attempt: 2,
      startedAt: "2026-09-16T16:10:24.100000+08:00",
      status: "running",
      required: true,
      priority: 0,
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].runId, "retry");
  assert.equal(rows[0].delegationId, "run:first");
  assert.equal(rows[0].startedAt, "2026-09-16T16:08:23.794020+08:00");
  assert.equal(rows[0].status, "running");
});
