/**
 * 规范化 tool calls（过滤无效项，统一字段形状）。
 * 可复用于流式合并后的调用列表与非流式 message.tool_calls。
 */
function normalizeToolCallsList(list) {
  const arr = Array.isArray(list) ? list : []
  return arr
    .filter((tc) => tc && (tc.id || '').trim() && (tc.function?.name || '').trim())
    .map((tc) => ({
      id: String(tc.id || '').trim(),
      type: tc.type || 'function',
      function: {
        name: (tc.function?.name || '').trim(),
        arguments: typeof tc.function?.arguments === 'string' ? tc.function.arguments : '',
      },
    }))
}

module.exports = {
  normalizeToolCallsList,
}
