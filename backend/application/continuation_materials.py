"""Initialize domain material documents and immutable per-entity provenance."""
import json
import hashlib
from collections import defaultdict
from application.creation_material_service import materials


FACT_LABELS = {
    'relationship_summary': '关系归纳', 'character_summary': '人物归纳', 'story_summary': '阶段概述',
    'character_identity': '人物身份', 'character_state': '人物状态',
    'character_knowledge': '人物知情', 'relationship': '人物关系',
    'world_rule': '世界规则', 'background': '故事背景', 'setting': '世界设定',
    'location': '地点', 'faction': '势力', 'item': '物品', 'event': '事件',
    'timeline': '时间线', 'unresolved_plot': '未决情节', 'foreshadowing': '伏笔',
}


def fact_markdown(record):
    value = record['value']
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if record.get('claimNature') == 'inference':
        text = '【推断，非原文明示】\n\n' + text
    evidence = '来源：' + '、'.join(dict.fromkeys(e.get('sectionTitle', e['sectionId']) for e in record['evidence']))
    return f"### {record['subjectKey']} · {FACT_LABELS.get(record['factKind'], '原作事实')}\n{record['predicate']}：{text}\n\n{evidence}"


async def initialize_materials(db, book_id, preview):
    groups = defaultdict(list)
    for record, mapping in zip(preview['records'], preview['materialMapping']):
        groups[(mapping['kind'], mapping['sourceKey'] if mapping['kind'] != 'background' else 'background')].append(record)
    for (kind, key), records in groups.items():
        body = '\n\n'.join(fact_markdown(record) for record in records)
        if kind == 'plot':
            identity = key
        elif kind == 'character':
            identity = await db.execute_and_get_id('INSERT INTO characters(book_id,name,profile_md) VALUES (?,?,?)', [book_id, key, ''])
        elif kind == 'entity':
            entity_types = {r['factKind'] for r in records} & {'location', 'faction', 'item'}
            entity_type = next(iter(entity_types)) if len(entity_types) == 1 else 'other'
            identity = await db.execute_and_get_id("INSERT INTO setting_entities(book_id,entity_type,name,profile_md) VALUES (?,?,?,?)", [book_id, entity_type, key, ''])
        else:
            identity = book_id
            await db.execute('INSERT INTO story_background(book_id,content) VALUES (?,?)', [book_id, ''])
        await db.execute('INSERT INTO continuation_material_baselines VALUES (?,?,?,?,?,?)',
            [book_id, kind, str(identity), key, body, json.dumps(records, ensure_ascii=False)])
    svc = materials(db)
    plot_entries = [{'title': '情节 · ' + key, 'sourceKey': key, 'path': '原作情节/' + hashlib.sha256(key.encode()).hexdigest()[:20] + '.md'}
                    for kind, key in groups if kind == 'plot']
    migration = await svc.preview(book_id)
    await svc.migrate(book_id, migration['sourceRevision'], source_links=plot_entries)
    await initialize_plot(db, book_id, preview, svc, plot_entries)
    await db.execute("UPDATE novel_knowledge_bindings SET scope_json=? WHERE book_id=?", [json.dumps({'purpose': 'discussion'}), book_id])


async def initialize_plot(db, book_id, source_preview, svc, plot_entries):
    """Keep the source snapshot in the Vault and seed editable planning records."""
    from infrastructure.obsidian.material_links import resolve_source_links, material_link
    from infrastructure.obsidian.material_document import render
    from infrastructure.obsidian.material_transaction import ensure_directory
    from database.crud.outlines import save_outline
    from infrastructure.persistence.writing.sqlite_story_memory_repository import SqliteStoryMemoryRepository
    from domains.writing.story_memory_ledger import StoryMemoryLedger
    from domains.writing.story_memory import SourceReference
    from domains.writing.story_settings import PlotThread, StorySettingChange

    entries = list(plot_entries)
    for item in await svc.snapshot(book_id):
        mapping = await db.fetch_one('SELECT path FROM creation_material_files WHERE book_id=? AND kind=? AND entity_id=?',
                                    [book_id, item['kind'], item['entityId']])
        entries.append({'path': mapping['path'], 'title': item['row'].get('name', '故事背景')})
    root = svc.directory(await svc.binding(book_id))
    if plot_entries:
        ensure_directory(root / '原作情节')
    rows = await db.fetch_all("SELECT * FROM continuation_material_baselines WHERE book_id=? AND kind='plot' ORDER BY source_key", [book_id])
    for row in rows:
        entry = next(item for item in plot_entries if item['sourceKey'] == row['source_key'])
        body = resolve_source_links(row['body'], entries)
        await db.execute("UPDATE continuation_material_baselines SET body=? WHERE book_id=? AND kind='plot' AND entity_id=?", [body, book_id, row['entity_id']])
        await svc.stage(root, entry['path'], None, render(
            {'title': entry['title'], 'type': 'plot', 'status': 'confirmed', 'temporal_scope': 'global',
             'source_analysis_id': source_preview['sourceAnalysisId']},
            '# 原作情节快照\n\n' + body + '\n\n> 此文件保留创建时的来源资料；续写发展在大纲和伏笔管理中维护。'))

    records = sorted((r for r in source_preview['records'] if r['factKind'] in {'event', 'timeline', 'unresolved_plot', 'foreshadowing'}),
                     key=lambda r: (max(e['sectionOrdinal'] for e in r['evidence']), r['sourceFactId']))
    def content(record):
        text = resolve_source_links(fact_markdown(record), entries)
        entry = next(item for item in plot_entries if item['sourceKey'] == record['subjectKey'])
        return text + '\n\n原作资料：' + material_link(entry['path'], entry['title'])
    history = [content(r) for r in records if r['factKind'] in {'event', 'timeline'}]
    latest_plots = {}
    for record in records:
        if record['factKind'] in {'unresolved_plot', 'foreshadowing'}:
            key = (record['factKind'], record['subjectKey'], record['predicate'])
            previous = latest_plots.get(key)
            # At the same source position, a resolved state takes precedence.
            if previous and previous.get('lifecycleStatus') == 'resolved' and max(e['sectionOrdinal'] for e in previous['evidence']) == max(e['sectionOrdinal'] for e in record['evidence']):
                continue
            latest_plots[key] = record
    open_plots = [r for r in latest_plots.values() if r.get('lifecycleStatus', 'active') == 'active']
    if records:
        await save_outline(db, {'book_id': book_id, 'type': 'global', 'title': '故事大纲',
            'markdown_content': '# 原作已发生情节\n\n' + '\n\n'.join(history) +
            '\n\n# 续写起点与待推进事项\n\n' + '\n\n'.join(content(r) for r in open_plots) + '\n\n# 后续剧情规划\n\n'})
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))
    for record in open_plots:
        section = max(record['evidence'], key=lambda e: e['sectionOrdinal'])
        chapter_id = 'source:' + book_id + ':' + section['sectionId']
        delta = await ledger.stage_settings(book_id=book_id, chapter_id=chapter_id,
            source_revision=source_preview['snapshotDigest'], source_type='continuation',
            changes=[StorySettingChange(
                setting=PlotThread(thread_id='source-' + record['sourceFactId'],
                    title=record['subjectKey'] + ' · ' + FACT_LABELS[record['factKind']],
                    summary=content(record), opened_chapter_id=chapter_id),
                source=SourceReference(chapter_id=chapter_id, excerpt=section['excerpt'],
                    locator={'analysisId': source_preview['sourceAnalysisId'], 'sourceFactId': record['sourceFactId']},
                    narrative_order=section['sectionOrdinal']))])
        await ledger.approve_delta(delta.id)
