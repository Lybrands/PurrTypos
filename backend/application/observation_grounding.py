"""Literal grounding metadata; never a semantic correctness verdict."""

import re

from domains.writing.techniques import TechniqueError


def quoted_text_outside_evidence(observation):
    evidence = [str(item.get('excerpt') or '') for item in observation.get('evidence', [])]
    quotes = re.findall(r'“([^”\n]+)”|「([^」\n]+)」', str(observation.get('bodyMarkdown') or ''))
    return list(dict.fromkeys(text for pair in quotes for text in pair
                             if text and not any(text in excerpt for excerpt in evidence)))


def evidence_context(evidence, text):
    lower = evidence.get('segmentStartCharacter', 0)
    upper = evidence.get('segmentEndCharacter', len(text))
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (lower, upper)) or not 0 <= lower < upper <= len(text):
        raise TechniqueError('invalid_reference', '观察证据范围无效')
    excerpt = str(evidence.get('excerpt') or '')
    locator = evidence.get('locator')
    if locator is None:
        offset = text.find(excerpt, lower, upper)
        start, end = offset, offset + len(excerpt)
    else:
        start, end = locator.get('start'), locator.get('end')
    if (not excerpt or not all(isinstance(n, int) and not isinstance(n, bool) for n in (start, end))
            or not lower <= start < end <= upper or text[start:end] != excerpt):
        raise TechniqueError('invalid_reference', '观察引文与来源定位不一致')
    context_start, context_end = max(lower, start - 120), min(upper, end + 120)
    return {'sectionId': evidence['sectionId'], 'startCharacter': context_start,
            'endCharacter': context_end, 'text': text[context_start:context_end],
            'excerptStartCharacter': start, 'excerptEndCharacter': end}
