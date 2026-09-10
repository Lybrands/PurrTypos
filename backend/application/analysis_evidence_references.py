"""Evidence references resolve only within durable, frozen unit input."""
from copy import deepcopy
from domains.novel_analysis import canonical_digest


def evidence_id(revision_id, evidence):
    return 'source-evidence:' + canonical_digest({'revisionId': revision_id,
        'sectionId': evidence.get('sectionId'), 'excerpt': str(evidence.get('excerpt') or '').strip(),
        'segmentStartCharacter': evidence.get('segmentStartCharacter'),
        'segmentEndCharacter': evidence.get('segmentEndCharacter')})


def evidence_index(payload):
    revision = payload.get('sourceRevisionId') or (payload.get('sourceBinding') or {}).get('sourceRevisionId')
    result = {}

    def visit(value):
        if isinstance(value, dict):
            for item in value.get('evidence', []):
                if isinstance(item, dict) and (item.get('excerpt') or item.get('referenceKind') == 'chapter') and item.get('sectionId'):
                    result[evidence_id(revision, item)] = deepcopy(item)
            for key, child in value.items():
                if key != 'evidence':
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(payload)
    return result


def model_evidence_index(payload):
    """Stable short handles within one frozen input; durable identities remain unchanged."""
    index = evidence_index(payload)
    scope = canonical_digest(sorted(index)).split(":")[-1][:8]
    return {f"E{scope}-{number}": evidence for number, (_, evidence)
            in enumerate(sorted(index.items()), 1)}


def reference_projection(value, revision, *, payload=None):
    frozen = payload if payload is not None else {"sourceRevisionId": revision, "value": value}
    aliases = {evidence_id(revision, item): key for key, item in model_evidence_index(frozen).items()}

    def visit(item):
        if isinstance(item, dict):
            return {key: ([{'evidenceId': aliases[evidence_id(revision, evidence)]} for evidence in child]
                          if key == 'evidence' else visit(child)) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        return item
    return visit(value)


def restore_references(value, payload):
    index = {**evidence_index(payload), **model_evidence_index(payload)}

    def visit(item):
        if isinstance(item, dict):
            if 'evidenceId' in item:
                if set(item) != {'evidenceId'} or item['evidenceId'] not in index:
                    raise ValueError(f"无效证据引用 {item.get('evidenceId')}，或混入额外字段。用 listAnalysisEvidence 查询当前有效编号，不猜测或修改编号。")
                return deepcopy(index[item['evidenceId']])
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        return item
    return visit(value)
