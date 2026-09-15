from application.sub_agent_result_presentation import present_sub_agent_result


def test_completed_result_keeps_all_readable_sections_without_raw_json():
    result = present_sub_agent_result(
        """已完成分析。

```json
{
  "summaryMarkdown": "整书总结。",
  "facts": [
    {"subjectKey": "林月", "value": {"profile_md": "人物资料。"}}
  ],
  "craftCards": [
    {"title": "章末钩子", "bodyMarkdown": "技法内容。"}
  ]
}
```
"""
    )

    assert "整书总结" in result
    assert "林月" in result
    assert "人物资料" in result
    assert "章末钩子" in result
    assert "技法内容" in result
    assert "{" not in result
    assert "}" not in result
