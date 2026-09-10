"""Model handles for observations; persistence retains content digests."""
from copy import deepcopy
from domains.novel_analysis import canonical_digest
from application.novel_analysis_source import AnalysisEvidenceInputError


def observation_index(payload):
    from application.writing_technique_generation_tools import unit_observations
    observations = unit_observations(payload)
    revision = payload.get('sourceRevisionId') or (payload.get('sourceBinding') or {}).get('sourceRevisionId')
    scope = canonical_digest([revision, sorted(item['contentDigest'] for item in observations)]).split(':')[-1][:8]
    return {f'O{scope}-{number}': item for number, item in enumerate(sorted(observations, key=lambda item: item['contentDigest']), 1)}


def resolve_observation_ids(ids, payload):
    index = observation_index(payload)
    available = {item['contentDigest'] for item in index.values()}
    invalid = [key for key in ids if key not in index and key not in available]
    if invalid:
        raise AnalysisEvidenceInputError('无效观察编号：' + ', '.join(invalid)
            + '。请用 listAnalysisObservations 查询有效编号后重试；不要猜测或修改编号。')
    return [index[key]['contentDigest'] if key in index else key for key in ids]


def restore_observation_references(value, payload):
    result = deepcopy(value)
    for card in result.get('craftCards', []):
        if 'mergedObservationIds' in card:
            card['mergedObservationIds'] = resolve_observation_ids(card['mergedObservationIds'], payload)
    if 'evidenceRefs' in result:
        result['evidenceRefs'] = resolve_observation_ids(result['evidenceRefs'], payload)
    return result


def project_observations(result, payload):
    result = deepcopy(result)
    handles = {item['contentDigest']: key for key, item in observation_index(payload).items()}
    for item in result.get('observations', []):
        item['observationId'] = handles[item.pop('contentDigest')]
    for item in result.get('grounding', []):
        item['observationId'] = handles[item['observationId']]
    return result
