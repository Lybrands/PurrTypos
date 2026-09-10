import Markdown from '../components/Markdown'
import { ArrowRightIcon, PurrButton } from '@/purr-components'
import type { NovelAnalysisCraftCard, NovelAnalysisEvidence } from '../types'

export default function WritingTechniqueEvidence({ scopeNotes, cards, onShowEvidence }: {
  scopeNotes: string[]
  cards: NovelAnalysisCraftCard[]
  onShowEvidence: (title: string, evidence: NovelAnalysisEvidence[], body: string) => void
}) {
  return <section className="novel-technique-evidence">
    <header>
      <div><strong>生成依据</strong><span>技法由 {cards.length} 条来源观察归纳，可随时回看对应原文。</span></div>
    </header>
    {scopeNotes.length > 0 && <details className="novel-technique-scope">
      <summary><span>适用范围</span><em>{scopeNotes.length} 条说明</em></summary>
      <div>{scopeNotes.map((note, index) => <p key={index}>{note}</p>)}</div>
    </details>}
    <div className="novel-technique-evidence-list">
      {cards.map((card, index) => <details key={card.id || `${card.title}:${index}`} className="novel-technique-evidence-card">
        <summary>
          <span>{String(index + 1).padStart(2, '0')}</span>
          <strong>{card.title}</strong>
          <em>{card.evidence.length} 条原文</em>
        </summary>
        <div>
          <Markdown>{card.bodyMarkdown}</Markdown>
          <PurrButton type="text" size="small" icon={<ArrowRightIcon />} iconPosition="end" onClick={() => onShowEvidence(card.title, card.evidence, card.bodyMarkdown)}>查看原文证据</PurrButton>
        </div>
      </details>)}
    </div>
  </section>
}
