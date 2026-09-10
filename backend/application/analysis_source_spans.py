"""Unit-local model handles; full source identities stay in the host."""
from copy import deepcopy
import hashlib
import re
from application.novel_analysis_source import NovelAnalysisSourceReader, AnalysisEvidenceInputError


async def source(db, state):
    payload = state.domain.get('unitInput', {})
    binding = payload.get('sourceBinding') or {}
    section = binding.get('sectionId')
    revision = state.domain.get('sourceRevisionId') or binding.get('sourceRevisionId')
    if not section or not revision:
        raise AnalysisEvidenceInputError('原文选择仅用于当前提取片段；汇总阶段使用 listAnalysisEvidence')
    if binding.get('sourceRevisionId', revision) != revision:
        raise AnalysisEvidenceInputError('来源版本与当前单元绑定不一致')
    row = await NovelAnalysisSourceReader(db).read_section(source_revision_id=revision,
        bound_section_ids=[section], section_id=section)
    low, high = int(binding.get('startCharacter', 0)), int(binding.get('endCharacter', len(row['text'])))
    if not 0 <= low <= high <= len(row['text']):
        raise AnalysisEvidenceInputError('原文范围与当前单元绑定不一致')
    frozen_text = (payload.get('sourceEvidence') or {}).get('text')
    if isinstance(frozen_text, str) and frozen_text != row['text'][low:high]:
        raise AnalysisEvidenceInputError('原文与冻结输入不一致，不能重新解释已有编号')
    return row, low, high


def span_id(revision, section, start, end, text):
    digest = hashlib.sha256(f"{revision}:{section}:{start}:{end}:{text}".encode()).hexdigest()[:12]
    return f'S{start}-{end}-{digest}'


def identity(row, start, end):
    return span_id(row['revisionId'], row['id'], start, end, row['text'][start:end])


def _catalog(text, revision, section, offset=0):
    """Reconstruct the same mapping after restart, independently of query order."""
    entries = {}
    for line in text.splitlines(keepends=True):
        for start in range(0, len(line), 160):
            chunk = line[start:start + 160]
            if chunk.strip():
                begin, end = offset + start, offset + start + len(chunk)
                entries[f'S{len(entries) + 1:03d}'] = {
                    'start': begin, 'end': end, 'excerpt': chunk,
                    'identity': span_id(revision, section, begin, end, chunk),
                }
        offset += len(line)
    return entries


def _public(handle, entry):
    return {'sourceSpanId': handle, 'excerpt': entry['excerpt']}


def annotate_source(payload):
    result = deepcopy(payload)
    binding = result.get('sourceBinding') or {}
    evidence = result.get('sourceEvidence') or {}
    text = evidence.get('text')
    if not isinstance(text, str) or not binding.get('sectionId'):
        return result
    entries = _catalog(text, binding['sourceRevisionId'], binding['sectionId'], int(binding.get('startCharacter') or 0))
    evidence.pop('text')
    evidence['excerpts'] = [_public(handle, entry) for handle, entry in entries.items()]
    result['sourceEvidence'] = evidence
    result['sourceAccess'] = 'sourceSpanId 仅在当前单元有效。引用只传 {sourceSpanId}，不重抄 excerpt；编号失效时用 findAnalysisSourceEvidence 查询，省略 query 可分页查看目录。'
    return result


async def find_spans(db, state, query='', *, offset=0, limit=8):
    row, low, high = await source(db, state)
    query = query.strip()
    if len(query) > 100 or offset < 0 or not 1 <= limit <= 8:
        raise AnalysisEvidenceInputError('关键词最多 100 字，offset 非负，limit 为 1–8')
    entries = _catalog(row['text'][low:high], row['revisionId'], row['id'], low)
    selected, mode = [], None
    if not query:
        selected = list(entries.items())
    else:
        # A match crossing a 160-character boundary returns both original
        # entries, rather than inventing a new query-dependent identity.
        hits, position = [], low
        while position < high:
            found = row['text'].find(query, position, high)
            if found < 0:
                break
            hits.append((found, found + len(query)))
            position = found + len(query)
        selected = [(handle, entry) for handle, entry in entries.items()
                    if any(entry['start'] < end and entry['end'] > start for start, end in hits)]
        if not selected:
            normalize = lambda value: re.sub(r'[^\w]', '', value).casefold()
            terms = [normalize(term) for term in re.split(r'\s+', query) if normalize(term)]
            ranked = [(sum(term in normalize(entry['excerpt']) for term in terms), handle, entry)
                      for handle, entry in entries.items()]
            selected = [(handle, entry) for score, handle, entry in sorted(ranked, key=lambda item: (-item[0], item[2]['start'])) if score]
            mode = 'keyword_candidate'
    page = selected[offset:offset + limit]
    return {
        'items': [{**_public(handle, entry), **({'matchMode': mode} if mode else {})} for handle, entry in page],
        'total': len(selected), 'nextOffset': offset + limit if offset + limit < len(selected) else None,
        'nextAction': '核对原文是否支持判断，只提交 {sourceSpanId}。无匹配时省略 query 分页查看目录，不猜编号。',
    }


def _invalid_handle(handle, entries):
    # Offer bounded, explicit candidates for legacy range mistakes, never
    # silently pick or repair an identity on the model's behalf.
    candidates = []
    match = re.match(r'S(\d+)-(\d+)-', handle)
    if match:
        start, end = map(int, match.groups())
        candidates = [(key, entry) for key, entry in entries.items()
                      if entry['start'] < end and entry['end'] > start][:2]
    listed = '；'.join(f'{key}: {entry["excerpt"][:80].strip()}' for key, entry in candidates)
    detail = f'可核对候选（未自动采用）：{listed}。' if listed else ''
    valid = f'S001–S{len(entries):03d}' if entries else '无可引用内容'
    return AnalysisEvidenceInputError(
        f'原文编号 {handle[:80]} 无效；当前目录 {valid}。{detail}'
        '调用 findAnalysisSourceEvidence，省略 query 可分页查看有效编号；仅修正失败条目，不猜测编号。')


async def expand_spans(db, state, value):
    result, cached, entries = deepcopy(value), None, None
    for candidate in [*result.get('facts', []), *result.get('craftCards', []), *([result['storyOverview']] if result.get('storyOverview') else [])]:
        for evidence in candidate.get('evidence', []):
            evidence.pop('_verifiedSpan', None)
            if 'sourceSpanId' not in evidence:
                continue
            if cached is None:
                cached = await source(db, state)
                row, low, high = cached
                entries = _catalog(row['text'][low:high], row['revisionId'], row['id'], low)
            row, low, high = cached
            handle = str(evidence['sourceSpanId'])
            entry = entries.get(handle)
            if entry:
                start, end, expected = entry['start'], entry['end'], entry['identity']
            else:
                match = re.fullmatch(r'S(\d+)-(\d+)-[0-9a-f]{12}', handle)
                if not match:
                    raise _invalid_handle(handle, entries)
                start, end = map(int, match.groups())
                expected = handle
            if not low <= start < end <= high or end - start > 160 or identity(row, start, end) != expected:
                raise _invalid_handle(handle, entries)
            # An explicit foreign section/revision must not be hidden by a valid
            # local handle. Display-only excerpt metadata remains compatible.
            if evidence.get('sectionId', row['id']) != row['id']:
                raise AnalysisEvidenceInputError('引用章节不属于当前单元，请使用当前目录')
            evidence.clear()
            evidence.update(sectionId=row['id'], excerpt=row['text'][start:end], segmentStartCharacter=start, segmentEndCharacter=end, _verifiedSpan=True)
    return result
