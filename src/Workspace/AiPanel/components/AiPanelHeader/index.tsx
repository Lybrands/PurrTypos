import { EllipsisOutlined } from "@ant-design/icons";
import { Button, Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";

interface AiPanelHeaderProps {
  menuItems: MenuProps["items"];
}

export default function AiPanelHeader({
  menuItems,
}: AiPanelHeaderProps) {
  return (
    <div className="panel-header">
      <span className="panel-title">AI 对话</span>
      <div className="panel-header-actions">
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
