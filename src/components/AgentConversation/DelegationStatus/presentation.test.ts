import assert from "node:assert/strict";
import test from "node:test";

import { presentableStructuredResponse } from "./presentation.ts";

test("structured screenplay child output exposes only its natural-language summary", () => {
  const content = JSON.stringify({
    assistantResponse: "已完成第五集正文。",
    scenes: [{ sceneId: "s05", sceneText: "INT. 房间 - 日" }],
  });

  assert.equal(
    presentableStructuredResponse(content),
    "已完成第五集正文。",
  );
});

test("partial structured screenplay child output stays hidden", () => {
  assert.equal(
    presentableStructuredResponse('{"scenes":[{"sceneId":"s05"'),
    "",
  );
  assert.equal(
    presentableStructuredResponse("```json\n{\n  \"scenes\":"),
    "",
  );
});

test("ordinary delegated Agent text remains visible", () => {
  assert.equal(
    presentableStructuredResponse("已检查第五集连续性。"),
    "已检查第五集连续性。",
  );
});

test("consecutive delegated operations collapse in the owning execution panel", async () => {
  const presentation = await import("./presentation.ts");

  assert.equal(typeof presentation.buildSubAgentTimelineItems, "function");
  const items = presentation.buildSubAgentTimelineItems([
    {
      type: "commentary",
      md: "针对第 6 集结构与节奏进行独立审阅。",
      regionKey: "child-commentary-0",
    },
    {
      type: "tools",
      segmentIndex: 0,
      segment: {
        commentaryBlockIndex: 0,
        labels: ["写入剧本候选稿", "检查剧本候选稿"],
        itemDurationsMs: [48, 28],
      },
    },
    {
      type: "tools",
      segmentIndex: 1,
      segment: {
        commentaryBlockIndex: null,
        labels: ["发布候选稿"],
        itemDurationsMs: [36],
      },
    },
  ], "delegation-review-6");

  assert.deepEqual(items.map((item: { type: string }) => item.type), [
    "commentary",
    "stepGroup",
  ]);
  assert.deepEqual(
    items[1].type === "stepGroup"
      ? items[1].parts
          .filter((part) => part.type === "tools")
          .flatMap((part) => part.segment.labels)
      : [],
    ["写入剧本候选稿", "检查剧本候选稿", "发布候选稿"],
  );
});
