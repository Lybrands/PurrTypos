import React from "react";
import type { PurrTextAreaRef } from '@/purr-components';

/**
 * 用户消息「就地编辑」的 textarea 机制：
 * - 记录当前正在编辑的消息下标（editingMessageIndex）；
 * - 持有编辑框 ref 与草稿 ref；
 * - 进入编辑时把光标移到文末并聚焦。
 *
 * 只负责编辑框本身的交互；真正的「编辑后重发」编排（doSubmit / 清空勾选等）仍由
 * 调用方持有，通过 getEditTextareaValue / setEditingMessageIndex 协作。
 */
export function useMessageEditing() {
  const [editingMessageIndex, setEditingMessageIndex] = React.useState<
    number | null
  >(null);
  const editTextareaRef = React.useRef<PurrTextAreaRef | null>(null);
  const editingMessageDraftRef = React.useRef("");

  const getEditTextareaValue = React.useCallback(() => {
    const el = editTextareaRef.current;
    if (!el) return "";
    const textarea =
      el.resizableTextArea?.textArea ??
      (el.nativeElement as HTMLTextAreaElement | null);
    return textarea?.value ?? "";
  }, []);

  React.useEffect(() => {
    if (editingMessageIndex == null) return;
    const raf = requestAnimationFrame(() => {
      const el = editTextareaRef.current;
      const textarea =
        el?.resizableTextArea?.textArea ??
        (el?.nativeElement as HTMLTextAreaElement | null);
      if (!textarea) return;
      const end = textarea.value.length;
      textarea.focus();
      textarea.setSelectionRange(end, end);
    });
    return () => cancelAnimationFrame(raf);
  }, [editingMessageIndex]);

  return {
    editingMessageIndex,
    setEditingMessageIndex,
    editTextareaRef,
    editingMessageDraftRef,
    getEditTextareaValue,
  };
}
