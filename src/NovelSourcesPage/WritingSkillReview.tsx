import Markdown from '../components/Markdown'
import type { NovelAnalysisArtifact } from '../types'

export function WritingSkillReview({ artifact }: { artifact: NovelAnalysisArtifact }) {
  const skill = artifact.writingSkill
  const report = artifact.distillation
  if (!skill || !report) return <p>尚未形成完整的蒸馏结果。</p>
  return <div className="novel-analysis-result-list">
    <p>{artifact.skillReviewStatus === 'pending_review'
      ? '模型复核通过，仍需人工审核适用性和试写效果。'
      : '复核发现问题，当前方法不能加入写作方法库。请查看问题后重新蒸馏。'}</p>
    <article><Markdown>{skill.markdown}</Markdown></article>
    <details><summary>复核结果与修订</summary>
      {report.assessment.checks.map(check => <p key={check.dimension}>
        <strong>{({ evidence: '证据支持', abstraction: '抽象程度', execution: '可执行性', transfer: '迁移效果', boundaries: '适用边界' } as Record<string, string>)[check.dimension]} · {check.passed ? '通过' : '需修订'}</strong>：{check.reason}
      </p>)}
      {report.revisionNotes.map((note, index) => <p key={index}>{note}</p>)}
    </details>
    {[['首次试写', report.initialTrials], ['修订后试写', report.transferTrials]].map(([label, value]) => {
      const trials = value as typeof report.transferTrials
      return <details key={String(label)}><summary>{String(label)}</summary>
        {trials.trials.map((trial, index) => <article key={index}>
          <strong>场景 {index + 1}</strong><p>{trial.brief}</p>
          <h4>普通写法</h4><Markdown>{trial.baseline}</Markdown>
          <h4>应用方法后</h4><Markdown>{trial.application}</Markdown>
          {trial.stepApplications.map(step => <p key={step.step}>步骤 {step.step}：{step.observation}</p>)}
        </article>)}
      </details>
    })}
  </div>
}
