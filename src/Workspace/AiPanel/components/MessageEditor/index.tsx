import React from "react";
import { Button, Input, type TextAreaRef } from "../../../../ui";
import type { AiModelConfig, EntityId } from "../../../../types";
import AiContextBar, { type AiContextBarBindings } from "../AiContextBar";
import AiComposeBottom, {
  type ModelSelectionBindings,
} from "../AiComposeBottom";

export interface MessageEditorProps {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  contextBar: AiContextBarBindings;
  modelConfigs: AiModelConfig[];
  modelSelection: ModelSelectionBindings;
  /** 编辑中条目的对话索引，用作 textarea key，确保切换编辑目标时重建。 */
  editingMessageIndex: number | null;
  editingMessageDraftRef: React.MutableRefObject<string>;
  editTextareaRef: React.RefObject<TextAreaRef | null>;
  onSend: () => void;
  onCancel: () => void;
  onAbort: () => void;
}

/**
 * 用户消息的“编辑重发”表单：关联上下文栏 + 文本域 + 底部模型条（取消 / 发送）。
 * 从 ChatMessageBubble 内联块抽出，让气泡组件本身只负责消息形态分发。
 */
export default function MessageEditor({
  bookId,
  chapterId,
  contextBar,
  modelConfigs,
  modelSelection,
  editingMessageIndex,
  editingMessageDraftRef,
  editTextareaRef,
  onSend,
  onCancel,
  onAbort,
}: MessageEditorProps) {
  return (
    <div className="bubble-content bubble-content--edit">
      {bookId != null && (
        <AiContextBar
          bookId={bookId}
          chapterId={chapterId ?? null}
          {...contextBar}
        />
      )}
      <Input.TextArea
        key={`edit-${editingMessageIndex}`}
        className="bubble-edit-textarea"
        defaultValue={editingMessageDraftRef.current}
        ref={editTextareaRef as React.RefObject<TextAreaRef>}
        placeholder="编辑内容，发送将从此处重新对话…"
        autoSize={{ minRows: 2, maxRows: 8 }}
        autoFocus
        onChange={(e) => {
          editingMessageDraftRef.current = e.target.value;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
      />
      <AiComposeBottom
        modelConfigs={modelConfigs}
        {...modelSelection}
        loading={false}
        onAbort={onAbort}
        rightContent={
          <div className="bubble-edit-actions">
            <Button type="text" size="small" onClick={onCancel}>
              取消
            </Button>
            <Button type="primary" size="small" onClick={onSend}>
              发送
            </Button>
          </div>
        }
      />
    </div>
  );
}
