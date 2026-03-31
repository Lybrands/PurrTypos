import test from "node:test";
import assert from "node:assert/strict";

import {
  appendAssistantTailMarkdown,
  getAssistantRenderableMarkdown,
  mergeAssistantErrorNotice,
} from "../rendering.ts";

test("renders collab tail markdown when tool calls exist but no post-tool content exists yet", () => {
  const wrapped = "\n\n### 最新段落（已写入正文）\n\n```text\n测试段落\n```\n";

  const next = appendAssistantTailMarkdown(
    {
      content: "",
      toolCallSegments: [{ textBefore: "", labels: ["编辑章节内容"] }],
    },
    wrapped,
  );

  assert.equal(getAssistantRenderableMarkdown(next), wrapped);
});

test("prefers post-tool content when tool calls already have visible markdown", () => {
  const wrapped = "\n\n### 最新段落（已写入正文）\n\n```text\n测试段落\n```\n";

  const rendered = getAssistantRenderableMarkdown({
    content: `前文${wrapped}`,
    contentAfterToolCalls: wrapped,
    toolCallSegments: [{ textBefore: "前文", labels: ["编辑章节内容"] }],
  });

  assert.equal(rendered, wrapped);
});

test("appends plain assistant markdown directly when no tool calls exist", () => {
  const next = appendAssistantTailMarkdown(
    {
      content: "已有回复",
      toolCallSegments: [],
    },
    "\n\n补充段落",
  );

  assert.equal(next.content, "已有回复\n\n补充段落");
  assert.equal(getAssistantRenderableMarkdown(next), "已有回复\n\n补充段落");
});

test("preserves streamed assistant content when an error arrives mid-response", () => {
  const next = mergeAssistantErrorNotice(
    {
      content: "这是已经生成的回答",
    },
    "网络中断",
  );

  assert.equal(next.content, "这是已经生成的回答\n\n> 生成中断：网络中断");
});

test("shows error detail when no assistant content exists yet", () => {
  const next = mergeAssistantErrorNotice(
    {
      content: "",
    },
    "网络中断",
  );

  assert.equal(next.content, "生成失败：网络中断");
  assert.equal(next.isError, true);
});

test("shows generic fallback when no content and no error text", () => {
  const next = mergeAssistantErrorNotice(
    {
      content: "",
    },
  );

  assert.equal(next.content, "本轮已结束，请继续下一条指令。");
  assert.equal(next.isError, true);
});
