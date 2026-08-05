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

test("ordinary child conversation text remains visible", () => {
  assert.equal(
    presentableStructuredResponse("已检查第五集连续性。"),
    "已检查第五集连续性。",
  );
});
