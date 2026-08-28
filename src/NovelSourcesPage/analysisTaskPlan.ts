import type {
  AgentConversationMessage,
  AiTaskPlan,
  AiTaskStep,
} from '../agent-runtime'
import type { NovelAnalysisRun } from '../types'

const RUNNING_UNIT_STATUSES = new Set(['running', 'claimed'])
const ACTIVE_RUN_STATUSES = new Set(['pending', 'running', 'claimed'])

function backendTimestampMs(value?: string | null): number | null {
  const normalized = String(value || '').trim().replace(' ', 'T')
  if (!normalized) return null
  const timestamp = Date.parse(
    /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized) ? normalized : `${normalized}Z`,
  )
  return Number.isFinite(timestamp) ? timestamp : null
}

export function buildNovelAnalysisTiming(
  run: NovelAnalysisRun,
  nowMs = Date.now(),
  monotonicNow = performance.now(),
): Pick<AgentConversationMessage, 'durationMs' | 'turnStartedAt'> {
  const startedAt = backendTimestampMs(run.createTime)
  const status = run.taskStatus || run.runStatus
  if (ACTIVE_RUN_STATUSES.has(status)) {
    const elapsed = startedAt == null ? 0 : Math.max(0, nowMs - startedAt)
    return { turnStartedAt: monotonicNow - elapsed }
  }
  const finishedAt = backendTimestampMs(run.updateTime)
  return startedAt == null || finishedAt == null
    ? {}
    : { durationMs: Math.max(0, finishedAt - startedAt) }
}

function taskStatus(run: NovelAnalysisRun): AiTaskPlan['status'] {
  const status = run.taskStatus || run.runStatus
  if (status === 'completed' || status === 'done') return 'done'
  if (status === 'paused') return 'paused'
  if (status === 'failed') return 'failed'
  if (status === 'canceled') return 'canceled'
  return 'running'
}

function unitStatus(
  units: NonNullable<NovelAnalysisRun['units']>,
): AiTaskStep['status'] {
  if (units.some((unit) => unit.status === 'failed')) return 'failed'
  if (units.length > 0 && units.every((unit) => unit.status === 'completed')) return 'done'
  if (units.some((unit) => RUNNING_UNIT_STATUSES.has(unit.status))) return 'running'
  return 'pending'
}

export function buildNovelAnalysisTaskPlan(run: NovelAnalysisRun): AiTaskPlan {
  const semanticPlan = run.analysisPlan
  if (semanticPlan?.steps.length) {
    return {
      runId: run.runId,
      title: semanticPlan.title || '分析计划',
      goal: semanticPlan.goal || semanticPlan.taskSpec?.goal,
      status: taskStatus(run),
      steps: semanticPlan.steps.map((step) => {
        const units = (run.units || []).filter(
          (unit) => unit.plannerStepId === step.id,
        )
        return {
          id: step.id,
          title: step.title,
          description: step.description,
          type: step.type === 'review' ? 'review' : 'analyze',
          executor: step.executor === 'tool' ? 'tool' : 'model',
          dependsOn: step.dependsOn,
          status: unitStatus(units),
          error: units.find((unit) => unit.errorCode)?.errorCode || undefined,
        }
      }),
    }
  }

  return {
    runId: run.runId,
    title: '分析进度',
    goal: '形成可审核的事实脉络和写作技法',
    status: taskStatus(run),
    steps: (run.units || []).map((unit) => ({
      id: unit.unitId,
      title: unit.title,
      type: unit.kind === 'build_review_artifact' ? 'review' : 'analyze',
      executor: unit.kind === 'validate_evidence' || unit.kind === 'coverage_report'
        ? 'tool'
        : 'model',
      status: unitStatus([unit]),
      error: unit.errorCode || undefined,
    })),
  }
}
