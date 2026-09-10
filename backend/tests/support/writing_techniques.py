from application.writing_technique_service import WritingTechniqueService

ENTRY = '---\nname: 信息释放\ndescription: 在行动中改变读者获得的信息。\n---\n先写行动，再写结果；将尚未确认的信息留给下一个选择。'


async def candidate(db, revision_id, observations, task_id='analysis-task', multifile=False):
    service = WritingTechniqueService(db)
    draft = await service.create_draft(operation_id='fixture:' + task_id, storage_scope='analysis_candidate',
        owner={'sourceRevisionId': revision_id, 'taskId': task_id})
    changes = [{'action': 'put', 'path': 'SKILL.md', 'content': ENTRY + ('\n[按需展开](细节.md)' if multifile else '')}]
    if multifile:
        changes.append({'action': 'put', 'path': '细节.md', 'content': '短句用于行动节点，解释放在后续停顿。'})
    draft = await service.apply_changes(draft['techniqueId'], draft['draftId'], expected_revision=0, operation_id='fixture:files', changes=changes)
    draft = await service.seal('technique', draft['techniqueId'], draft['draftId'], expected_revision=draft['draftRevision'],
        expected_tree_digest=draft['treeDigest'], operation_id='fixture:seal')
    return {'status': 'generated', 'candidate': {'techniqueId': draft['techniqueId'], 'draftId': draft['draftId'], 'versionId': draft['sealedRef']['versionId']},
            'evidenceRefs': [item['contentDigest'] for item in observations], 'scopeNotes': ['局部片段支持的机制。'], 'reason': ''}
