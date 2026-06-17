import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import React from "react";
import type { AiTaskPlan, AiTaskStep } from "../../hooks/chat.types";
import "./index.scss";

export interface TaskPlanCardProps {
  plan: AiTaskPlan;
}

function TaskStepIcon({ status }: { status: AiTaskStep["status"] }) {
  if (status === "done") {
    return <CheckCircleOutlined className="task-plan-step__icon" />;
  }
  if (status === "running") {
    return <LoadingOutlined className="task-plan-step__icon" />;
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

function TaskPlanCard({ plan }: TaskPlanCardProps) {
  const total = plan.steps.length;

  return (
    <div className={`task-plan-card task-plan-card--${plan.status}`}>
      <div className="task-plan-card__header">
        <UnorderedListOutlined className="task-plan-card__header-icon" />
        <span>To-dos</span>
        <span className="task-plan-card__count">{total}</span>
      </div>
      <div className="task-plan-card__steps">
        {plan.steps.map((step, idx) => (
          <TaskStepItem key={step.id || idx} step={step} />
        ))}
      </div>
    </div>
  );
}

export default React.memo(TaskPlanCard);
