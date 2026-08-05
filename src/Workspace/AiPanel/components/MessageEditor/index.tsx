import React from "react";
import { type PurrTextAreaRef } from '@/purr-components';
import AgentMessageEditor from '@/components/AgentConversation/MessageEditor';
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
  editTextareaRef: React.RefObject<PurrTextAreaRef | null>;
  onSend: (content: string) => void;
  onCancel: () => void;
  onAbort: () => void;
}

/**
 * 小说业务对共享编辑器的适配层：只注入关联上下文与模型栏，
 * 编辑器结构、文本域、按钮和键盘行为由 AgentMessageEditor 统一负责。
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
    <AgentMessageEditor
      key={`edit-${editingMessageIndex}`}
      initialContent={editingMessageDraftRef.current}
      textareaRef={editTextareaRef}
      beforeEditor={bookId != null ? (
        <AiContextBar
          bookId={bookId}
          chapterId={chapterId ?? null}
          {...contextBar}
        />
      ) : undefined}
      onDraftChange={(content) => {
        editingMessageDraftRef.current = content;
      }}
      onSubmit={(content) => {
        editingMessageDraftRef.current = content;
        onSend(content);
      }}
      onCancel={onCancel}
      renderFooter={(actions) => (
        <AiComposeBottom
          modelConfigs={modelConfigs}
          {...modelSelection}
          loading={false}
          onAbort={onAbort}
          rightContent={actions}
        />
      )}
    />
  );
}
