import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  RightOutlined,
} from "../../../../ui";
import React from "react";
import type { AiTaskPlan, AiTaskStep } from "../../hooks/chat.types";
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

export function getTaskPlanProgress(plan: AiTaskPlan) {
  const total = plan.steps.length;
  const completed = plan.steps.filter((step) => step.status === "done").length;
  const currentStep =
    plan.steps.find((step) => step.status === "running") ||
    plan.steps.find(
      (step) => step.status === "blocked" || step.status === "failed",
    ) ||
    plan.steps.find((step) => step.status === "pending");
  return {
    total,
    completed,
    currentStep,
    percent: total > 0 ? Math.round((completed / total) * 100) : 0,
  };
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
    return <CheckCircleOutlined className="task-plan-step__icon" />;
  }
  if (status === "running") {
    return <LoadingOutlined spin className="task-plan-step__icon" />;
  }
  if (status === "blocked") {
    return <PauseCircleOutlined className="task-plan-step__icon" />;
  }
  if (status === "failed") {
    return <CloseCircleOutlined className="task-plan-step__icon" />;
  }
  return <ClockCircleOutlined className="task-plan-step__icon" />;
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
  return (
    <div className="task-plan-card__steps">
      {plan.steps.map((step, idx) => (
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
  const { total, completed, currentStep, percent } = getTaskPlanProgress(plan);

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
        <RightOutlined className="task-plan-card__chevron" />
        <span className="task-plan-card__label">{getTaskPlanLabel(plan)}</span>
        <span className="task-plan-card__count">
          {completed}/{total}
        </span>
        {currentStep && !terminal ? (
          <span className="task-plan-card__current">· {currentStep.title}</span>
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
