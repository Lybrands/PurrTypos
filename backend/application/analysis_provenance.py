"""Evidence granularity for source-derived material; creative input is separate."""
from application.novel_analysis_source import AnalysisEvidenceInputError

SUMMARY_KINDS = {'background', 'character_summary', 'relationship_summary', 'story_summary', 'setting', 'location', 'faction', 'item'}

CRITICAL_KINDS = {'character_identity', 'character_state', 'character_knowledge', 'relationship', 'world_rule', 'event', 'timeline', 'unresolved_plot', 'foreshadowing'}


def validate_policy(item, *, overview=False, craft=False):
    nature = item.get('claimNature', 'fact')
    if nature not in {'fact', 'summary', 'inference'}:
        raise AnalysisEvidenceInputError('原作分析只接受事实、综合归纳或推断；新增设定与未来方案请保存为创作资料')
    chapter = any(e.get('referenceKind') == 'chapter' and not e.get('excerpt') for e in item.get('evidence', []))
    if chapter and (craft or (not overview and (nature == 'fact' or item.get('factKind') not in SUMMARY_KINDS or item.get('factKind') in CRITICAL_KINDS))):
        raise AnalysisEvidenceInputError('此条必须精确引文：调用 findAnalysisSourceEvidence 选择 {sourceSpanId}，或改用 {referenceKind:"quote",excerpt:"逐字原文"}；保留正确引文，不要删除它只留 chapter')
    if not item.get('evidence'):
        raise AnalysisEvidenceInputError('原作资料需要引文或章节来源')


async def validate_reference(reader, revision, sections, raw):
    if raw.get('referenceKind') != 'chapter' or raw.get('excerpt'):
        return await reader.validate_excerpt(source_revision_id=revision, bound_section_ids=sections,
            section_id=str(raw.get('sectionId') or ''), excerpt=str(raw.get('excerpt') or ''),
            start_character=raw.get('segmentStartCharacter'), end_character=raw.get('segmentEndCharacter'))
    identity = str(raw.get('sectionId') or '')
    if identity not in sections:
        raise AnalysisEvidenceInputError('章节来源不属于当前分析范围')
    return await reader.chapter_reference(revision, sections, identity)
