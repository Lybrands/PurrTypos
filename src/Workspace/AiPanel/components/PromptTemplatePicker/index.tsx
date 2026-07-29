import { services } from '@/services'
import React from "react";
import { FileTextOutlined, SettingOutlined } from "../../../../ui";
import { Button, Empty, Popover, Tooltip, useConfirm, useToast } from "../../../../ui";
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
  const appMessage = useToast();
  const confirm = useConfirm();
  const [open, setOpen] = React.useState(false);
  const [managerOpen, setManagerOpen] = React.useState(false);
  const [userTemplates, setUserTemplates] = React.useState<AiPromptTemplate[]>(
    [],
  );
  const [loading, setLoading] = React.useState(false);

  const loadUser = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await services.promptTemplates.listPromptTemplates();
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
    async (tpl: PromptTemplateItem) => {
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
      const result = await confirm({
        title: "输入框已有内容",
        content: `要如何插入「${tpl.title}」？`,
        confirmText: "替换",
        confirmVariant: "danger",
        actions: [{ id: 'append', label: '追加' }],
      });
      if (result === 'confirm') doInsert(filled);
      if (result === 'append') {
        const sep = currentPrompt.endsWith("\n") ? "" : "\n\n";
        doInsert(currentPrompt + sep + filled);
      }
    },
    [appMessage, confirm, context, currentPrompt, onInsert],
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
      <Tooltip title="插入提示词模版">
        <span className="purr-popup-trigger">
          <Popover
            open={open}
            onOpenChange={setOpen}
            arrow={false}
            placement="topLeft"
            overlayClassName="prompt-template-popover"
            content={content}
          >
          <Button
            type="text"
            size="small"
            icon={<FileTextOutlined style={{ fontSize: 14 }} />}
            className="ai-context-icon-btn"
            disabled={disabled}
            aria-label="插入提示词模版"
          />
          </Popover>
        </span>
      </Tooltip>
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
