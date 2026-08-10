import {
  CheckCircleIcon,
  CloseCircleIcon,
  LoadingIcon,
  PauseCircleIcon,
  PurrButton,
  PurrPopover,
  type PurrPopoverProps,
} from '@/purr-components'
import type { AiTaskPlan } from '../../agent-runtime'
import {
  getTaskPlanLabel,
  TaskPlanSteps,
} from '../../Workspace/AiPanel/components/TaskPlanCard'
import {
  getTaskPlanCountLabel,
  getTaskPlanProgress,
} from '../../Workspace/AiPanel/taskPlanSelection'
import { localizeTaskPlan } from './localization'
import './index.scss'

interface AgentTaskProgressProps {
  plan: AiTaskPlan
  placement?: PurrPopoverProps['placement']
}

function TaskPlanStatusIcon({ plan }: AgentTaskProgressProps) {
  if (plan.status === 'done') return <CheckCircleIcon />
  if (plan.status === 'blocked' || plan.status === 'paused') return <PauseCircleIcon />
  if (plan.status === 'failed' || plan.status === 'canceled') return <CloseCircleIcon />
  return <LoadingIcon spin />
}

export default function AgentTaskProgress({
  plan,
  placement = 'bottomRight',
}: AgentTaskProgressProps) {
  const displayPlan = localizeTaskPlan(plan)
  const {
    runningSteps,
    currentStep,
    percent,
  } = getTaskPlanProgress(displayPlan)
  const label = displayPlan.status === 'running' || displayPlan.status === 'planned'
    ? '执行中'
    : getTaskPlanLabel(displayPlan)
  const parallel = runningSteps.length > 1
  const countLabel = getTaskPlanCountLabel(displayPlan)
  const currentLabel = currentStep
    ? parallel ? `${currentStep.title} 等` : currentStep.title
    : ''

  return (
    <PurrPopover
      trigger="click"
      placement={placement}
      content={(
        <div className="ai-task-progress-popover">
          <div className="ai-task-progress-popover__header">
            <span className="ai-task-progress-popover__title">
              {displayPlan.title || '任务进度'}
            </span>
            <span className="ai-task-progress-popover__count">
              {countLabel}
            </span>
          </div>
          {currentStep ? (
            <div className="ai-task-progress-popover__current">
              当前：{currentLabel}
            </div>
          ) : null}
          <div className="ai-task-progress-popover__progress">
            <span style={{ width: `${percent}%` }} />
          </div>
          <TaskPlanSteps plan={displayPlan} />
        </div>
      )}
    >
      <PurrButton
        type="text"
        size="small"
        className={`ai-task-progress-trigger ai-task-progress-trigger--${displayPlan.status}`}
        icon={<TaskPlanStatusIcon plan={displayPlan} />}
      >
        <span className="ai-task-progress-trigger__status">{label}</span>
        {currentStep ? (
          <span className="ai-task-progress-trigger__current">{currentLabel}</span>
        ) : null}
        <span className="ai-task-progress-trigger__count">
          {countLabel}
        </span>
      </PurrButton>
    </PurrPopover>
  )
}
