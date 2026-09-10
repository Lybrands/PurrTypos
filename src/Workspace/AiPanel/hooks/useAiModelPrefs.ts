import React from "react";
import { services } from "../../../services";
import type { AiModelConfig, EntityId } from "../../../types";
import { loadModelPrefs, saveModelPrefs } from "../utils";

const validModelIds = (configs: AiModelConfig[]) => configs.map((c) => c.id);

export function useAiModelPrefs(
  bookId: EntityId | null | undefined,
  modelConfigs: AiModelConfig[],
) {
  const prefBookId = bookId ?? null;
  const ids = React.useMemo(() => validModelIds(modelConfigs), [modelConfigs]);
  const initialPrefs = React.useMemo(
    () => loadModelPrefs(prefBookId, ids),
    [prefBookId, ids.join(",")],
  );
  const [selection, setSelection] = React.useState({ bookId: prefBookId, model: initialPrefs.model });
  const selectedModel = selection.bookId === prefBookId && ids.includes(selection.model)
    ? selection.model : initialPrefs.model;
  const setSelectedModel = React.useCallback((value: React.SetStateAction<string>) => {
    setSelection((previous) => ({
      bookId: prefBookId,
      model: typeof value === 'function'
        ? value(previous.bookId === prefBookId ? previous.model : initialPrefs.model) : value,
    }));
  }, [prefBookId, initialPrefs.model]);
  const modelSaveQueue = React.useRef(Promise.resolve());
  const [chatAgentMode, setChatAgentMode] = React.useState(
    initialPrefs.chatAgentMode,
  );
  const selectedModelConfig = React.useMemo(
    () => modelConfigs.find((c) => c.id === selectedModel) ?? null,
    [modelConfigs, selectedModel],
  );
  React.useEffect(() => {
    setChatAgentMode(initialPrefs.chatAgentMode);
  }, [prefBookId]);

  React.useEffect(() => {
    if (prefBookId == null || !selectedModel) return;
    const bookModel = { [`writing_current_model:${prefBookId}`]: selectedModel };
    modelSaveQueue.current = modelSaveQueue.current
      .then(async () => {
        const result = await services.settings.setSettings(bookModel);
        if (!result.success) throw new Error('保存当前作品模型失败');
      })
      .catch((error) => console.error('保存当前作品模型失败', error));
  }, [prefBookId, selectedModel]);

  React.useEffect(() => {
    saveModelPrefs(prefBookId, selectedModel, chatAgentMode);
  }, [prefBookId, selectedModel, chatAgentMode]);

  return {
    selectedModel,
    setSelectedModel,
    chatAgentMode,
    setChatAgentMode,
    selectedModelConfig,
  };
}
