import React from "react";
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
  const [selectedModel, setSelectedModel] = React.useState<string>(
    initialPrefs.model,
  );
  const [chatAgentMode, setChatAgentMode] = React.useState(
    initialPrefs.chatAgentMode,
  );
  const selectedModelConfig = React.useMemo(
    () => modelConfigs.find((c) => c.id === selectedModel) ?? null,
    [modelConfigs, selectedModel],
  );
  const modelConfigsRecord = React.useMemo(() => {
    const r: Record<string, { label?: string; max_tokens?: number }> = {};
    modelConfigs.forEach((c) => {
      r[c.id] = { label: c.name };
    });
    return r;
  }, [modelConfigs]);

  React.useEffect(() => {
    const prefs = loadModelPrefs(prefBookId, ids);
    setSelectedModel(prefs.model);
    setChatAgentMode(prefs.chatAgentMode);
  }, [prefBookId, ids.join(",")]);

  React.useEffect(() => {
    if (ids.length && !ids.includes(selectedModel)) {
      setSelectedModel(ids[0]);
    }
    if (ids.length === 0 && selectedModel) {
      setSelectedModel("");
    }
  }, [ids, selectedModel]);

  React.useEffect(() => {
    saveModelPrefs(prefBookId, selectedModel, chatAgentMode);
  }, [prefBookId, selectedModel, chatAgentMode]);

  return {
    selectedModel,
    setSelectedModel,
    chatAgentMode,
    setChatAgentMode,
    selectedModelConfig,
    modelConfigsRecord,
  };
}
