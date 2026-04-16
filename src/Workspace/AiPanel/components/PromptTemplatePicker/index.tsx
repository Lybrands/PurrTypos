import React from "react";
import { App as AntdApp, Button, Empty, Popover, Tooltip } from "antd";
import { FileTextOutlined, SettingOutlined } from "@ant-design/icons";
import {
  BUILTIN_PROMPT_TEMPLATES,
  PROMPT_PLACEHOLDERS,
  applyPromptPlaceholders,
  hasUnfilledPlaceholders,
  type PromptTemplateContext,
  type PromptTemplateItem,
} from "../../promptTemplates";
import type { AiPromptTemplate } from "../../../../types";
import PromptTemplateManagerModal from "../PromptTemplateManagerModal";
import "./index.scss";

export interface PromptTemplatePickerProps {
  /** 当前输入框内容：非空时插入需二次确认 */
  currentPrompt: string;
  /** 插入文本到输入框（已完成占位符替换） */
  onInsert: (text: string) => void;
  /** 自动填充上下文 */
  context: PromptTemplateContext;
  disabled?: boolean;
}

export default function PromptTemplatePicker({
  currentPrompt,
  onInsert,
  context,
  disabled,
}: PromptTemplatePickerProps) {
  const { message: appMessage, modal } = AntdApp.useApp();
  const [open, setOpen] = React.useState(false);
  const [managerOpen, setManagerOpen] = React.useState(false);
  const [userTemplates, setUserTemplates] = React.useState<AiPromptTemplate[]>(
    [],
  );
  const [loading, setLoading] = React.useState(false);

  const loadUser = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await window.electronAPI.listPromptTemplates();
      if (res.success) setUserTemplates(res.data ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (open) loadUser();
  }, [open, loadUser]);

  const allTemplates: PromptTemplateItem[] = React.useMemo(
    () => [
      ...BUILTIN_PROMPT_TEMPLATES,
      ...userTemplates.map((t) => ({
        id: t.id,
        title: t.title,
        content: t.content,
        sort: t.sort,
        builtin: false,
      })),
    ],
    [userTemplates],
  );

  const handlePick = React.useCallback(
    (tpl: PromptTemplateItem) => {
      const filled = applyPromptPlaceholders(tpl.content, context);
      const missing = hasUnfilledPlaceholders(tpl.content, context);
      const doInsert = (text: string) => {
        onInsert(text);
        setOpen(false);
        if (missing.length > 0) {
          appMessage.warning(
            `已插入，但以下占位暂无数据：${missing.join("、")}`,
          );
        }
      };

      const trimmed = currentPrompt.trim();
      if (!trimmed) {
        doInsert(filled);
        return;
      }
      const pending: { destroy?: () => void } = {};
      const inst = modal.confirm({
        title: "输入框已有内容",
        content: `要如何插入「${tpl.title}」？`,
        okText: "替换",
        cancelText: "取消",
        okButtonProps: { danger: true },
        footer: (_, { OkBtn, CancelBtn }) => (
          <>
            <CancelBtn />
            <Button
              onClick={() => {
                const sep = currentPrompt.endsWith("\n") ? "" : "\n\n";
                doInsert(currentPrompt + sep + filled);
                pending.destroy?.();
              }}
            >
              追加
            </Button>
            <OkBtn />
          </>
        ),
        onOk: () => doInsert(filled),
      });
      pending.destroy = inst.destroy;
    },
    [appMessage, context, currentPrompt, modal, onInsert],
  );

  const content = (
    <div className="prompt-template-popover-content">
      <div className="prompt-template-popover-header">
        <span className="prompt-template-popover-title">提示词模版</span>
        <Tooltip title="管理我的模版">
          <Button
            type="text"
            size="small"
            icon={<SettingOutlined style={{ fontSize: 13 }} />}
            onClick={() => {
              setOpen(false);
              setManagerOpen(true);
            }}
          />
        </Tooltip>
      </div>

      {loading && userTemplates.length === 0 ? (
        <div className="prompt-template-loading">加载中…</div>
      ) : allTemplates.length === 0 ? (
        <Empty
          image={false}
          description="暂无模版，点击右上设置图标新增"
          className="prompt-template-empty"
        />
      ) : (
        <>
          <div className="prompt-template-section">
            <div className="prompt-template-section-label">内置</div>
            <ul className="prompt-template-list">
              {BUILTIN_PROMPT_TEMPLATES.map((t) => (
                <li key={`b-${t.id}`}>
                  <Tooltip
                    placement="left"
                    mouseEnterDelay={0.5}
                    title={
                      <div className="prompt-template-preview">{t.content}</div>
                    }
                    styles={{ root: { maxWidth: 360 } }}
                  >
                    <button
                      type="button"
                      className="prompt-template-item"
                      onClick={() => handlePick(t)}
                    >
                      <span className="prompt-template-item-title">
                        {t.title}
                      </span>
                    </button>
                  </Tooltip>
                </li>
              ))}
            </ul>
          </div>

          {userTemplates.length > 0 && (
            <div className="prompt-template-section">
              <div className="prompt-template-section-label">我的</div>
              <ul className="prompt-template-list">
                {userTemplates.map((t) => (
                  <li key={`u-${t.id}`}>
                    <Tooltip
                      placement="left"
                      mouseEnterDelay={0.5}
                      title={
                        <div className="prompt-template-preview">
                          {t.content || "(空)"}
                        </div>
                      }
                      styles={{ root: { maxWidth: 360 } }}
                    >
                      <button
                        type="button"
                        className="prompt-template-item"
                        onClick={() =>
                          handlePick({
                            id: t.id,
                            title: t.title,
                            content: t.content,
                            sort: t.sort,
                            builtin: false,
                          })
                        }
                      >
                        <span className="prompt-template-item-title">
                          {t.title}
                        </span>
                      </button>
                    </Tooltip>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="prompt-template-popover-footer">
        可用占位符：
        {PROMPT_PLACEHOLDERS.map((p, i) => (
          <React.Fragment key={p.token}>
            {i > 0 ? " " : null}
            <Tooltip title={p.description}>
              <code>{p.token}</code>
            </Tooltip>
          </React.Fragment>
        ))}
      </div>
    </div>
  );

  return (
    <>
      <Popover
        trigger="click"
        open={open}
        onOpenChange={setOpen}
        arrow={false}
        placement="topLeft"
        overlayClassName="prompt-template-popover"
        content={content}
        destroyTooltipOnHide
      >
        <Tooltip title="插入提示词模版">
          <Button
            type="text"
            size="small"
            icon={<FileTextOutlined style={{ fontSize: 14 }} />}
            className="ai-context-icon-btn"
            disabled={disabled}
          />
        </Tooltip>
      </Popover>
      <PromptTemplateManagerModal
        open={managerOpen}
        onClose={() => {
          setManagerOpen(false);
          loadUser();
        }}
      />
    </>
  );
}
