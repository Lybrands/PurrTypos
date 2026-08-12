import React from 'react'
import { VideoCameraIcon } from '@/purr-components'
import type { AgentConversationExtensions } from '../components/AgentConversation'
import type {
  ScreenplayConversationState,
  ScreenplayTurnArtifact,
} from './conversationState'

interface ScreenplayConversationExtensionBindings {
  projectTitle: string
  stageLabel: string
  messages: ScreenplayConversationState['messages'] | undefined
  artifacts: ReadonlyMap<string, ScreenplayTurnArtifact>
  renderArtifact(artifact: ScreenplayTurnArtifact): React.ReactNode
}

export function useScreenplayConversationExtensions({
  projectTitle,
  stageLabel,
  messages,
  artifacts,
  renderArtifact,
}: ScreenplayConversationExtensionBindings): AgentConversationExtensions {
  return React.useMemo(() => ({
    renderSessionContext: () => (
      <div className="screenplay-conversation-context">
        <span><VideoCameraIcon /></span>
        <div>
          <small>当前项目</small>
          <strong title={projectTitle}>{projectTitle}</strong>
          <span>{stageLabel}</span>
        </div>
      </div>
    ),
    renderAssistantAttachment: (_message, index) => {
      const entry = messages?.[index]
      const artifact = entry?.role === 'assistant'
        ? artifacts.get(entry.turnId)
        : undefined
      return artifact ? renderArtifact(artifact) : null
    },
  }), [artifacts, messages, projectTitle, renderArtifact, stageLabel])
}
