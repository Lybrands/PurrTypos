import {
  MoreIcon,
} from '@/purr-components';
import { PurrButton, PurrDropdown, PurrTooltip, type PurrDropdownItem } from '@/purr-components';

interface AiPanelHeaderProps {
  menuItems: PurrDropdownItem[];
}

export default function AiPanelHeader({
  menuItems,
}: AiPanelHeaderProps) {
  return (
    <div className="panel-header">
      <span className="panel-title">AI 对话</span>
      <div className="panel-header-actions">
        <PurrDropdown menu={{ items: menuItems }} trigger={["click"]}>
          <PurrTooltip title="更多" mouseEnterDelay={0.5}>
            <PurrButton
              type="text"
              size="small"
              icon={<MoreIcon style={{ fontSize: 16 }} />}
              className="panel-header-action-btn"
            />
          </PurrTooltip>
        </PurrDropdown>
      </div>
    </div>
  );
}
