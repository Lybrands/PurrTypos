import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  EllipsisOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { Button, Dropdown, Popover, Tooltip } from "antd";
import type { MenuProps } from "antd";
import type { AiTaskPlan } from "../../hooks/chat.types";
import {
  getTaskPlanLabel,
  getTaskPlanProgress,
  TaskPlanSteps,
} from "../TaskPlanCard";
import "./index.scss";

interface AiPanelHeaderProps {
  menuItems: MenuProps["items"];
  activeTaskPlan?: AiTaskPlan;
}

function TaskPlanStatusIcon({ plan }: { plan: AiTaskPlan }) {
  if (plan.status === "done") return <CheckCircleOutlined />;
  if (plan.status === "blocked" || plan.status === "paused") {
    return <PauseCircleOutlined />;
  }
  if (plan.status === "failed" || plan.status === "canceled") {
    return <CloseCircleOutlined />;
  }
  return <LoadingOutlined spin />;
}

function HeaderTaskProgress({ plan }: { plan: AiTaskPlan }) {
  const { completed, total, currentStep, percent } = getTaskPlanProgress(plan);
  const headerLabel =
    plan.status === "running" || plan.status === "planned"
      ? "执行中"
      : getTaskPlanLabel(plan);

  return (
    <Popover
      trigger="click"
      placement="bottomRight"
      content={
        <div className="ai-task-progress-popover">
          <div className="ai-task-progress-popover__header">
            <span className="ai-task-progress-popover__title">
              {plan.title || "任务进度"}
            </span>
            <span className="ai-task-progress-popover__count">
              {completed}/{total}
            </span>
          </div>
          {currentStep ? (
            <div className="ai-task-progress-popover__current">
              当前：{currentStep.title}
            </div>
          ) : null}
          <div className="ai-task-progress-popover__progress">
            <span style={{ width: `${percent}%` }} />
          </div>
          <TaskPlanSteps plan={plan} />
        </div>
      }
    >
      <Button
        type="text"
        size="small"
        className={`ai-task-progress-trigger ai-task-progress-trigger--${plan.status}`}
        icon={<TaskPlanStatusIcon plan={plan} />}
      >
        <span>{headerLabel}</span>
        <span className="ai-task-progress-trigger__count">
          {completed}/{total}
        </span>
      </Button>
    </Popover>
  );
}

export default function AiPanelHeader({
  menuItems,
  activeTaskPlan,
}: AiPanelHeaderProps) {
  return (
    <div className="panel-header">
      <span className="panel-title">AI 对话</span>
      <div className="panel-header-actions">
        {activeTaskPlan ? <HeaderTaskProgress plan={activeTaskPlan} /> : null}
        <Dropdown menu={{ items: menuItems }} trigger={["click"]}>
          <Tooltip title="更多" mouseEnterDelay={0.5}>
            <Button
              type="text"
              size="small"
              icon={<EllipsisOutlined style={{ fontSize: 16 }} />}
              className="panel-header-action-btn"
            />
          </Tooltip>
        </Dropdown>
      </div>
    </div>
  );
}
