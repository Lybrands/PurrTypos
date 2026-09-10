import { PurrButton, PurrCheckbox, PurrTooltip, RobotIcon } from '@/purr-components'
import type { WritingTechniqueChoice, WritingTechniqueSelection } from '../../../services/writingTechniques'
import './WritingTechniquePicker.scss'

export default function WritingTechniquePicker({ sessionId, choices, selection, onSetMode, onToggle }: {
  sessionId: number | null; choices: WritingTechniqueChoice[]; selection: WritingTechniqueSelection
  onSetMode(mode: 'manual' | 'auto'): void; onToggle(id: string): void
}) {
  const auto = selection.mode === 'auto'
  return <section className="writing-technique-picker" aria-label="本轮写作技法与方案">
    <div className="writing-technique-picker__header">
      <div><strong>写作技法</strong>{selection.refs.length > 0 && <span>已选 {selection.refs.length} 项</span>}</div>
      <PurrTooltip title={auto ? '自动选择已开启' : '自动选择已关闭，仅使用手动勾选项'}>
        <PurrButton
          type="text"
          size="small"
          className={`writing-technique-picker__auto${auto ? ' is-active' : ''}`}
          icon={<RobotIcon />}
          aria-label={auto ? '关闭写作技法自动选择' : '开启写作技法自动选择'}
          aria-pressed={auto}
          disabled={sessionId == null}
          onClick={() => onSetMode(auto ? 'manual' : 'auto')}
        />
      </PurrTooltip>
    </div>
    <div className="writing-technique-picker__list">
      {choices.length === 0 && <span className="writing-technique-picker__empty">暂无可用的写作技法或方案</span>}
      {choices.map(choice => <div className="writing-technique-picker__item" key={choice.ref.id}>
        <PurrCheckbox disabled={sessionId == null} checked={selection.refs.some(ref => ref.id === choice.ref.id)} onChange={() => onToggle(choice.ref.id)}>
          <span className="writing-technique-picker__item-copy"><strong>{choice.name}</strong><small>{choice.ref.kind === 'scheme' ? '方案' : '技法'}{choice.description ? ` · ${choice.description}` : ''}</small></span>
        </PurrCheckbox>
      </div>)}
    </div>
  </section>
}
