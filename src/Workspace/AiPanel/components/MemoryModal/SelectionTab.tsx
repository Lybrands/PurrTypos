import React from 'react'
import { PurrCheckbox } from '@/purr-components'
import { PurrEmpty, PurrSpin } from '@/purr-components'
import type { MemoryModalController } from './useMemoryModal'
import SparkIdeaMeta from './SparkIdeaMeta'

interface SelectionTabProps {
  controller: MemoryModalController
}

export default function SelectionTab({ controller }: SelectionTabProps) {
  const {
    loading,
    sparkIdeas,
    longTermMemories,
    sparkIdeasByLayer,
    foreshadowing,
    checkedIds,
    checkedLongTermMemoryIds,
    checkedForeshadowingIds,
    chapterTitleById,
    characterNameById,
    handleToggle,
    handleToggleLongTermMemory,
    handleToggleForeshadowing,
  } = controller

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 24 }}>
        <PurrSpin />
      </div>
    )
  }

  if (longTermMemories.length === 0 && sparkIdeas.length === 0 && foreshadowing.length === 0) {
    return (
      <PurrEmpty
        image={false}
        description="暂无可强制注入的旧设定/伏笔，可在导演笔记本的「记忆 / 伏笔」中管理长期记忆"
      />
    )
  }

  return (
    <div className="memory-select-list">
      {longTermMemories.length > 0 && (
        <div className="memory-layer-block">
          <div className="memory-layer-title">长期记忆</div>
          {longTermMemories.map((memory) => {
            const checked = checkedLongTermMemoryIds.includes(memory.id)
            return (
              <div key={`long-term-${memory.id}`} className="memory-select-item">
                <PurrCheckbox
                  checked={checked}
                  onChange={(event) =>
                    handleToggleLongTermMemory(memory.id, event.target.checked)
                  }
                />
                <span
                  className="memory-select-content"
                  role="button"
                  tabIndex={0}
                  onClick={() => handleToggleLongTermMemory(memory.id, !checked)}
                  onKeyDown={(event) =>
                    event.key === 'Enter' && handleToggleLongTermMemory(memory.id, !checked)
                  }
                >
                  {memory.content || memory.summary || '（无内容）'}
                </span>
              </div>
            )
          })}
        </div>
      )}
      {sparkIdeasByLayer.map(({ layer, list }) =>
        list.length === 0 ? null : (
          <div key={layer} className="memory-layer-block">
            <div className="memory-layer-title">{layer}设定</div>
            {list.map((memory) => {
              const checked = checkedIds.includes(memory.id)
              return (
                <div key={memory.id} className="memory-select-item">
                  <PurrCheckbox
                    checked={checked}
                    onChange={(event) => handleToggle(memory.id, event.target.checked)}
                  />
                  <span
                    className="memory-select-content"
                    role="button"
                    tabIndex={0}
                    onClick={() => handleToggle(memory.id, !checked)}
                    onKeyDown={(event) =>
                      event.key === 'Enter' && handleToggle(memory.id, !checked)
                    }
                  >
                    {memory.content || '（无内容）'}
                    <SparkIdeaMeta
                      memory={memory}
                      chapterTitleById={chapterTitleById}
                      characterNameById={characterNameById}
                    />
                  </span>
                </div>
              )
            })}
          </div>
        )
      )}
      {foreshadowing.length > 0 && (
        <div className="memory-layer-block">
          <div className="memory-layer-title">伏笔记忆</div>
          {foreshadowing.map((item) => {
            const checked = checkedForeshadowingIds.includes(item.id)
            return (
              <div key={`f-${item.id}`} className="memory-select-item">
                <PurrCheckbox
                  checked={checked}
                  onChange={(event) =>
                    handleToggleForeshadowing(item.id, event.target.checked)
                  }
                />
                <span
                  className="memory-select-content"
                  role="button"
                  tabIndex={0}
                  onClick={() => handleToggleForeshadowing(item.id, !checked)}
                  onKeyDown={(event) =>
                    event.key === 'Enter' && handleToggleForeshadowing(item.id, !checked)
                  }
                >
                  {item.content || '（无内容）'}
                  <span className="memory-foreshadow-meta memory-foreshadow-meta--inline">
                    【{chapterTitleById.get(String(item.chapter_id)) ?? item.chapter_id} ·{' '}
                    {item.type}】
                  </span>
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
