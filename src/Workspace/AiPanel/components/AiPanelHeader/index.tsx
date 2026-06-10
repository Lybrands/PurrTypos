import {
  CompressOutlined,
  EllipsisOutlined,
  ExpandOutlined,
} from "@ant-design/icons";
import { Button, Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";

interface AiPanelHeaderProps {
  isMain: boolean;
  onSetMain: () => void;
  menuItems: MenuProps["items"];
}

export default function AiPanelHeader({
  isMain,
  onSetMain,
  menuItems,
}: AiPanelHeaderProps) {
  return (
    <div className="panel-header">
      <span className="panel-title">AI 对话</span>
      <div className="panel-header-actions">
        <Button
          type="text"
          size="small"
          icon={
            isMain ? (
              <CompressOutlined style={{ fontSize: 16 }} />
            ) : (
              <ExpandOutlined style={{ fontSize: 16 }} />
            )
          }
          title={isMain ? "已是主区域" : "扩大此区域为主"}
          onClick={onSetMain}
        />
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
