"""Conservative single-paragraph constraints for explicitly requested prose."""

import re

from purra.contracts import ResponseValidationResult


def requests_single_prose_paragraph(text: str) -> bool:
    text = str(text or "")
    if re.search(r"代码|程序|脚本|SQL|分段|多段|[两二三四五六七八九2-9]段", text.replace("不要分段", ""), re.I):
        return False
    requests = re.findall(r"(?:写|创作|续写)(?:一|1)段", text)
    return len(requests) == 1 and bool(re.search(r"场景|回忆|故事|对白|描写", text))


class SingleProseParagraphValidator:
    def validate(self, *, content, messages):
        text = str(content or "").strip()
        if not text:
            return ResponseValidationResult()
        paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
        if len(paragraphs) == 1:
            return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code="writing_paragraph_count_mismatch",
            repair_guidance="用户要求一段正文，当前正文分成了多段。保留原有内容、情绪落点和结束位置，"
                            "整理为一个连续自然段，不加标题、说明或额外情节。",
        )
