import {
  CheckCircleIcon,
  ClockIcon,
  CloseCircleIcon,
  LoadingIcon,
  PauseCircleIcon,
  ChevronRightIcon,
} from '@/purr-components';
import React from "react";
import type { AiTaskPlan, AiTaskStep } from "../../hooks/chat.types";
import {
  getTaskPlanProgress,
  getVisibleTaskPlanSteps,
} from "../../taskPlanSelection";
import "./index.scss";

export interface TaskPlanCardProps {
  plan: AiTaskPlan;
  planKey?: string;
}

type OpenState = {
  open: boolean;
  manuallySet: boolean;
};

const openStateStore = new Map<string, OpenState>();

export function isTaskPlanTerminal(plan: AiTaskPlan): boolean {
  return (
    plan.status === "done" ||
    plan.status === "blocked" ||
    plan.status === "failed" ||
    plan.status === "canceled"
  );
}

export function getTaskPlanLabel(plan: AiTaskPlan): string {
  if (plan.status === "done") return "已完成任务";
  if (plan.status === "blocked") return "任务受阻";
  if (plan.status === "failed") return "任务失败";
  if (plan.status === "canceled") return "任务已取消";
  if (plan.status === "paused") return "任务已暂停";
  return "任务进度";
}

function TaskStepIcon({ status }: { status: AiTaskStep["status"] }) {
  if (status === "done") {
    return <CheckCircleIcon className="task-plan-step__icon" />;
  }
  if (status === "running") {
    return <LoadingIcon spin className="task-plan-step__icon" />;
  }
  if (status === "blocked") {
    return <PauseCircleIcon className="task-plan-step__icon" />;
  }
  if (status === "failed") {
    return <CloseCircleIcon className="task-plan-step__icon" />;
  }
  return <ClockIcon className="task-plan-step__icon" />;
}

function TaskStepItem({ step }: { step: AiTaskStep }) {
  return (
    <div className={`task-plan-step task-plan-step--${step.status}`}>
      <TaskStepIcon status={step.status} />
      <span className="task-plan-step__title">{step.title}</span>
    </div>
  );
}

export function TaskPlanSteps({ plan }: { plan: AiTaskPlan }) {
  const steps = getVisibleTaskPlanSteps(plan);
  return (
    <div className="task-plan-card__steps">
      {steps.map((step, idx) => (
        <TaskStepItem key={step.id || idx} step={step} />
      ))}
    </div>
  );
}

function TaskPlanCard({ plan, planKey }: TaskPlanCardProps) {
  const terminal = isTaskPlanTerminal(plan);
  const stateKey = planKey || plan.title || "local-task-plan";
  const storedState = openStateStore.get(stateKey);
  const [expanded, setExpanded] = React.useState(
    () => storedState?.open ?? !terminal,
  );
  const manuallySetRef = React.useRef(storedState?.manuallySet ?? false);
  const previousTerminalRef = React.useRef(terminal);
  const {
    total,
    completed,
    runningSteps,
    currentStep,
    currentStepNumber,
    percent,
  } = getTaskPlanProgress(plan);
  const parallel = runningSteps.length > 1;

  React.useEffect(() => {
    const stored = openStateStore.get(stateKey);
    manuallySetRef.current = stored?.manuallySet ?? false;
    setExpanded(stored?.open ?? !terminal);
    previousTerminalRef.current = terminal;
  }, [stateKey]);

  React.useEffect(() => {
    const wasTerminal = previousTerminalRef.current;
    previousTerminalRef.current = terminal;
    if (manuallySetRef.current || wasTerminal === terminal) return;
    const nextOpen = !terminal;
    openStateStore.set(stateKey, { open: nextOpen, manuallySet: false });
    setExpanded(nextOpen);
  }, [stateKey, terminal]);

  const toggleExpanded = () => {
    const nextOpen = !expanded;
    manuallySetRef.current = true;
    openStateStore.set(stateKey, { open: nextOpen, manuallySet: true });
    setExpanded(nextOpen);
  };

  return (
    <section
      className={`task-plan-card task-plan-card--${plan.status} ${expanded ? "task-plan-card--open" : ""}`}
    >
      <button
        type="button"
        className="task-plan-card__header"
        onClick={toggleExpanded}
        aria-expanded={expanded}
      >
        <ChevronRightIcon className="task-plan-card__chevron" />
        <span className="task-plan-card__label">{getTaskPlanLabel(plan)}</span>
        <span className="task-plan-card__count">
          {!terminal && parallel
            ? `并行 ${runningSteps.length} 项 · 已完成 ${completed}/${total}`
            : !terminal && currentStepNumber != null
              ? `第 ${currentStepNumber}/${total} 步`
            : `已完成 ${completed}/${total}`}
        </span>
        {currentStep && !terminal ? (
          <span className="task-plan-card__current">
            · {parallel ? `${currentStep.title} 等` : currentStep.title}
          </span>
        ) : null}
      </button>
      <div className="task-plan-card__progress" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="task-plan-card__collapsible">
        <div className="task-plan-card__body">
          <TaskPlanSteps plan={plan} />
        </div>
      </div>
    </section>
  );
}

export default React.memo(TaskPlanCard);
