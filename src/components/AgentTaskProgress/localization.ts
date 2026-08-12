import type { AiTaskPlan, AiTaskStep } from '../../agent-runtime'
import { KNOWN_TOOL_CALL_LABELS } from '../AgentConversation/toolCallLabels.ts'

const HAN_TEXT = /[\u3400-\u9fff]/
const LATIN_TEXT = /[A-Za-z]/

const DEFAULT_STEP_TITLES: Record<AiTaskStep['type'], string> = {
  read: '读取所需内容',
  analyze: '分析整理',
  write: '生成内容',
  review: '检查并回复',
  confirm: '等待确认',
}

function needsChineseFallback(value: string): boolean {
  return LATIN_TEXT.test(value) && !HAN_TEXT.test(value)
}

function knownToolTitle(step: AiTaskStep): string | undefined {
  const toolName = step.suggestedTools?.[0]
  if (!toolName) return undefined
  return (KNOWN_TOOL_CALL_LABELS as Record<string, string>)[toolName]
}

export function localizeTaskStep(step: AiTaskStep): AiTaskStep {
  const title = String(step.title || '').trim()
  if (!needsChineseFallback(title)) return step
  return {
    ...step,
    title: knownToolTitle(step) || DEFAULT_STEP_TITLES[step.type] || '执行任务步骤',
  }
}

export function localizeTaskPlan(plan: AiTaskPlan): AiTaskPlan {
  const title = String(plan.title || '').trim()
  return {
    ...plan,
    title: needsChineseFallback(title) ? '任务进度' : title || '任务进度',
    steps: plan.steps.map(localizeTaskStep),
  }
}
