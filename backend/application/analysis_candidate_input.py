"""Normalize empty model extras without dropping meaningful candidate content."""
from copy import deepcopy
from application.novel_analysis_source import AnalysisEvidenceInputError


def clean_candidate(candidate, field):
    from application.novel_analysis_tools import _ANALYSIS_RESULT_SCHEMA
    properties = _ANALYSIS_RESULT_SCHEMA['properties'][field]['items']['properties']
    result = deepcopy(candidate)
    for key in set(result) - set(properties):
        value = result[key]
        if value is None or isinstance(value, str) and not value.strip():
            del result[key]
        else:
            raise AnalysisEvidenceInputError(f'未声明字段 {key} 含有内容，请整理到现有字段后重试；未保存该条目')
    return result


def clean_result(value):
    result = deepcopy(value)
    for field in ('facts', 'craftCards'):
        if field in result:
            result[field] = [clean_candidate(candidate, field) for candidate in result[field]]
    return result
