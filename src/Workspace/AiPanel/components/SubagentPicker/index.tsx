import React from "react";
import { Dropdown, Button } from "antd";
import type { MenuProps } from "antd";
import { DownOutlined } from "@ant-design/icons";
import {
  WRITING_SUBAGENT_OPTIONS,
  type WritingSubagentRole,
} from "../../pipelineStages";
import "./index.scss";

export interface SubagentPickerProps {
  /** Current selection for the next send; null = main writing */
  value: WritingSubagentRole | null;
  onChange: (v: WritingSubagentRole | null) => void;
  disabled?: boolean;
}

export default function SubagentPicker({
  value,
  onChange,
  disabled,
}: SubagentPickerProps) {
  const items: MenuProps["items"] = React.useMemo(
    () => [
      {
        key: "main",
        label: "主写（默认）",
        onClick: () => onChange(null),
      },
      { type: "divider" },
      ...WRITING_SUBAGENT_OPTIONS.map((o) => ({
        key: o.value,
        label: o.label,
        onClick: () => onChange(o.value),
      })),
    ],
    [onChange],
  );

  const label =
    value == null
      ? "主写"
      : WRITING_SUBAGENT_OPTIONS.find((o) => o.value === value)?.label ??
        "子专家";

  return (
    <Dropdown menu={{ items }} trigger={["click"]} disabled={disabled}>
      <Button size="small" className="subagent-picker-btn" disabled={disabled}>
        {label}
        <DownOutlined style={{ fontSize: 10, marginLeft: 4 }} />
      </Button>
    </Dropdown>
  );
}
