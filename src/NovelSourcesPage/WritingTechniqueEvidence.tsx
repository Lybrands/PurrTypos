import Markdown from '../components/Markdown'
import type { NovelAnalysisCraftCard } from '../types'

export default function WritingTechniqueEvidence({ scopeNotes, cards }: {
  scopeNotes: string[]
  cards: NovelAnalysisCraftCard[]
}) {
  return <section className="novel-technique-evidence">
    <header>
      <div><strong>分析观察</strong><span>技法由 {cards.length} 条整书观察归纳。</span></div>
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
        </summary>
        <div>
          <Markdown>{card.bodyMarkdown}</Markdown>
        </div>
      </details>)}
    </div>
  </section>
}
