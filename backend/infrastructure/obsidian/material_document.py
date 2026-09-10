"""Editable material documents use the same frontmatter contract as retrieval."""
import re
import yaml
from infrastructure.obsidian.reader import parse

KINDS = {'character', 'entity', 'background'}
ENTITY_TYPES = {'location', 'faction', 'item', 'other'}


def baseline_prefix(body):
    return '# 原作继承基线（只读）\n\n' + body + '\n\n# 本书后续发展\n\n'


def decode(raw, path, *, material_id=None, kind=None):
    if len(raw) > 1024 * 1024:
        raise ValueError('单条资料不能超过 1 MiB')
    doc = parse(raw, path)
    meta = doc['metadata']
    if meta.get('purr_schema') != 1 or meta.get('purr_kind') not in KINDS:
        raise ValueError('资料格式或类型无效')
    if not isinstance(meta.get('purr_id'), str) or not meta['purr_id']:
        raise ValueError('资料缺少稳定标识')
    if material_id is not None and meta['purr_id'] != material_id:
        raise ValueError('资料标识已改变')
    if kind is not None and meta['purr_kind'] != kind:
        raise ValueError('资料类型已改变')
    if not isinstance(meta.get('title'), str) or not isinstance(meta.get('purr_tags', ''), str):
        raise ValueError('标题和标签必须是文本')
    if meta['purr_kind'] == 'entity' and meta.get('type') not in ENTITY_TYPES:
        raise ValueError('设定类型无效')
    baseline = meta.get('purr_baseline')
    if baseline is not None:
        if not isinstance(baseline, str) or not doc['body'].startswith(baseline_prefix(baseline)):
            raise ValueError('原作继承基线不能修改')
    return doc


def create(material_id, kind, row, *, baseline=None):
    meta = {'purr_schema': 1, 'purr_id': material_id, 'purr_kind': kind, 'purr_origin': 'creation',
            'type': row.get('entity_type', kind), 'title': row.get('name', '故事背景'),
            'purr_tags': row.get('tags') or '', 'status': 'confirmed'}
    body = row.get('content') if kind == 'background' else row.get('profile_md')
    if baseline is not None:
        meta['purr_baseline'] = baseline
        meta['purr_baseline_origin'] = 'source_analysis'
        meta['temporal_scope'] = 'global'
        body = baseline_prefix(baseline) + (body or '')
    return render(meta, body)


def render(meta, body):
    return ('---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + '---\n' + (body or '')).encode('utf-8')


def edit(raw, path, data):
    doc = decode(raw, path)
    meta = dict(doc['metadata'])
    # Leave the original frontmatter byte-for-byte when only the body changes.
    for field, key in [('name', 'title'), ('tags', 'purr_tags'), ('entity_type', 'type')]:
        if field in data:
            meta[key] = data[field]
    body = data.get('content', data.get('profile_md', values(doc).get('content', values(doc).get('profile_md', ''))))
    if meta.get('purr_baseline') is not None:
        body = baseline_prefix(meta['purr_baseline']) + body
    if meta == doc['metadata']:
        text = raw.decode('utf-8-sig')
        match = re.match(r'^---\r?\n.*?\r?\n---(?:\r?\n|$)', text, re.S)
        result = (text[:match.end()] + body).encode('utf-8')
    else:
        result = render(meta, body)
    decode(result, path)
    return result


def values(doc):
    meta = doc['metadata']
    body = doc['body']
    if meta.get('purr_baseline') is not None:
        body = body[len(baseline_prefix(meta['purr_baseline'])):]
    if meta['purr_kind'] == 'background':
        return {'content': body}
    result = {'name': meta['title'], 'tags': meta.get('purr_tags', ''), 'profile_md': body}
    if meta['purr_kind'] == 'entity':
        result['entity_type'] = meta['type']
    return result
