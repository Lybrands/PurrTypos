import { services } from '@/services'
import React from "react";
import { PurrButton, PurrEmpty, PurrForm, PurrInput, PurrList, PurrModal, PurrPopconfirm, PurrSpace, PurrTag, PurrTooltip, usePurrToast, type PurrTextAreaRef } from '@/purr-components';
import {
  DeleteIcon,
  EditIcon,
  PlusIcon,
} from '@/purr-components';
import type { AiPromptTemplate } from "../../../../types";
import { PROMPT_PLACEHOLDERS } from "../../promptTemplates";
import "./index.scss";

export interface PromptTemplateManagerModalProps {
  open: boolean;
  onClose: () => void;
}

interface FormState {
  id: number | null;
  title: string;
  content: string;
}

const EMPTY_FORM: FormState = { id: null, title: "", content: "" };

export default function PromptTemplateManagerModal({
  open,
  onClose,
}: PromptTemplateManagerModalProps) {
  const appMessage = usePurrToast();
  const [templates, setTemplates] = React.useState<AiPromptTemplate[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [form, setForm] = React.useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = React.useState(false);
  const contentRef = React.useRef<PurrTextAreaRef | null>(null);

  const insertPlaceholder = React.useCallback((token: string) => {
    const el = contentRef.current;
    const textarea =
      el?.resizableTextArea?.textArea ??
      (el?.nativeElement as HTMLTextAreaElement | null);
    setForm((s) => {
      const current = s.content ?? "";
      const start = textarea?.selectionStart ?? current.length;
      const end = textarea?.selectionEnd ?? current.length;
      const next = current.slice(0, start) + token + current.slice(end);
      requestAnimationFrame(() => {
        if (!textarea) return;
        textarea.focus();
        const caret = start + token.length;
        textarea.setSelectionRange(caret, caret);
      });
      return { ...s, content: next };
    });
  }, []);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await services.promptTemplates.listPromptTemplates();
      if (res.success) setTemplates(res.data ?? []);
      else appMessage.error(res.error ?? "加载失败");
    } finally {
      setLoading(false);
    }
  }, [appMessage]);

  React.useEffect(() => {
    if (open) {
      setForm(EMPTY_FORM);
      load();
    }
  }, [open, load]);

  const isEditing = form.id != null;

  const handleSave = React.useCallback(async () => {
    const title = form.title.trim();
    const content = form.content;
    if (!title) {
      appMessage.warning("请输入标题");
      return;
    }
    setSaving(true);
    try {
      if (form.id != null) {
        const res = await services.promptTemplates.updatePromptTemplate({
          id: form.id,
          data: { title, content },
        });
        if (!res.success) {
          appMessage.error(res.error ?? "保存失败");
          return;
        }
        appMessage.success("已更新");
      } else {
        const res = await services.promptTemplates.createPromptTemplate({
          title,
          content,
        });
        if (!res.success) {
          appMessage.error(res.error ?? "保存失败");
          return;
        }
        appMessage.success("已新增");
      }
      setForm(EMPTY_FORM);
      await load();
    } finally {
      setSaving(false);
    }
  }, [appMessage, form, load]);

  const handleDelete = React.useCallback(
    async (id: number) => {
      const res = await services.promptTemplates.deletePromptTemplate({ id });
      if (!res.success) {
        appMessage.error(res.error ?? "删除失败");
        return;
      }
      appMessage.success("已删除");
      if (form.id === id) setForm(EMPTY_FORM);
      await load();
    },
    [appMessage, form.id, load],
  );

  return (
    <PurrModal
      open={open}
      onCancel={onClose}
      title="管理提示词模版"
      footer={null}
      width={720}
      destroyOnHidden
      className="prompt-template-manager-modal"
    >
      <div className="prompt-template-manager-body">
        <div className="prompt-template-manager-left">
          <div className="prompt-template-manager-left-header">
            <span>我的模版</span>
            <PurrButton
              size="small"
              type="text"
              icon={<PlusIcon />}
              onClick={() => setForm(EMPTY_FORM)}
            >
              新增
            </PurrButton>
          </div>
          <PurrList
            size="small"
            loading={loading}
            locale={{
              emptyText: (
                <PurrEmpty
                  image={false}
                  description="还没有自定义模版，右侧填写后保存"
                />
              ),
            }}
            dataSource={templates}
            renderItem={(item) => {
              const active = form.id === item.id;
              return (
                <PurrList.Item
                  className={`prompt-template-manager-item ${active ? "is-active" : ""}`}
                  actions={[
                    <PurrTooltip key="edit" title="编辑">
                      <PurrButton
                        type="text"
                        size="small"
                        icon={<EditIcon />}
                        onClick={() =>
                          setForm({
                            id: item.id,
                            title: item.title,
                            content: item.content,
                          })
                        }
                      />
                    </PurrTooltip>,
                    <PurrPopconfirm
                      key="del"
                      title="删除模版？"
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                      onConfirm={() => handleDelete(item.id)}
                    >
                      <PurrButton
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteIcon />}
                      />
                    </PurrPopconfirm>,
                  ]}
                >
                  <PurrList.Item.Meta
                    title={
                      <span
                        className="prompt-template-manager-item-title"
                        title={item.title}
                      >
                        {item.title}
                      </span>
                    }
                    description={
                      <span className="prompt-template-manager-item-desc">
                        {(item.content || "").slice(0, 40) || "（无内容）"}
                      </span>
                    }
                  />
                </PurrList.Item>
              );
            }}
          />
        </div>

        <div className="prompt-template-manager-right">
          <div className="prompt-template-manager-right-header">
            {isEditing ? "编辑模版" : "新建模版"}
          </div>
          <PurrForm layout="vertical" className="prompt-template-manager-form">
            <PurrForm.Item label="标题" required>
              <PurrInput
                value={form.title}
                maxLength={80}
                placeholder="例如：续写本章 / 审校穿帮"
                onChange={(e) =>
                  setForm((s) => ({ ...s, title: e.target.value }))
                }
              />
            </PurrForm.Item>
            <PurrForm.Item label="内容">
              <PurrInput.TextArea
                ref={contentRef}
                value={form.content}
                autoSize={{ minRows: 8, maxRows: 16 }}
                placeholder="在这里写下你的提示词模版…下方点击占位符即可快捷插入到光标处。"
                onChange={(e) =>
                  setForm((s) => ({ ...s, content: e.target.value }))
                }
              />
              <div className="prompt-template-placeholder-row">
                <span className="prompt-template-placeholder-label">
                  快捷插入占位符
                </span>
                <div className="prompt-template-placeholder-chips">
                  {PROMPT_PLACEHOLDERS.map((p) => (
                    <PurrTooltip key={p.token} title={p.description}>
                      <PurrTag
                        className="prompt-template-placeholder-chip"
                        onClick={() => insertPlaceholder(p.token)}
                      >
                        {p.token}
                      </PurrTag>
                    </PurrTooltip>
                  ))}
                </div>
                <span className="prompt-template-placeholder-hint">
                  发送时会自动替换为当前上下文
                </span>
              </div>
            </PurrForm.Item>
            <PurrForm.Item>
              <PurrSpace>
                <PurrButton
                  type="primary"
                  loading={saving}
                  onClick={handleSave}
                  disabled={!form.title.trim()}
                >
                  {isEditing ? "保存修改" : "创建模版"}
                </PurrButton>
                {isEditing && (
                  <PurrButton onClick={() => setForm(EMPTY_FORM)}>取消编辑</PurrButton>
                )}
              </PurrSpace>
            </PurrForm.Item>
          </PurrForm>
        </div>
      </div>
    </PurrModal>
  );
}
