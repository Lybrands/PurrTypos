import asyncio
import json
from types import SimpleNamespace

import pytest

from application.novel_analysis_tools import analysis_unit_input_text, load_unit_model_result
from application.novel_analysis_executor import _preserve_observations
from application.writing_technique_generation_tools import generation_registrations, unit_observations
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from domains.writing.techniques import TechniqueError
from tests.support.writing_techniques import ENTRY


def test_unsupported_observation_can_explain_absence_but_not_support_generated_content():
    from domains.writing_technique_generation import validate_technique_result
    observations = [{'contentDigest': 'unsupported', 'supportStatus': 'unsupported'}]
    result = {'status': 'generated', 'candidate': {'techniqueId': 't', 'draftId': 'd', 'versionId': 'v'},
        'evidenceRefs': ['unsupported'], 'scopeNotes': [], 'reason': ''}
    with pytest.raises(TechniqueError, match='不支持'):
        validate_technique_result(result, observations)
    absent = {**result, 'status': 'insufficient_material', 'candidate': None, 'reason': '没有相关写法'}
    assert validate_technique_result(absent, observations) == absent


@pytest.fixture
async def scope(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    service = WritingTechniqueService(db)
    draft = await service.create_draft(operation_id='generation', storage_scope='analysis_candidate', owner={'taskId': 'task', 'sourceRevisionId': 'source'})
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('unit','running','novel_source_analysis.unit','source',?)", [json.dumps({'unitId': 'skill:draft', 'taskId': 'task'})])
    observations = [{'contentDigest': 'observation', 'cardKind': '节奏', 'title': '信息释放', 'bodyMarkdown': '来源观察', 'evidence': []}]
    state = SimpleNamespace(run_id='unit', domain={'unitInput': {'stage': 'distill_skill', 'observations': observations, 'techniqueDraft': draft}})
    tools = {tool.schema.name: tool for tool in generation_registrations(db)}
    try:
        yield db, service, state, tools
    finally:
        await db.close()


async def invoke(tools, state, name, arguments, call_id):
    result = await tools[name].call_handler(state, arguments, SimpleNamespace(id=call_id), asyncio.Event())
    return result, json.loads(result.content)


async def test_fact_batches_validate_once_persist_idempotently_and_survive_empty_submission(scope):
    from tests.test_continuations import _published_analysis
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('fact-unit','running','novel_source_analysis.unit',?,'{}')", [revision['id']])
    state.run_id = 'fact-unit'
    state.domain = {'sourceRevisionId': revision['id'], 'sectionIds': [section['id']], 'interactionKind': 'unit', 'analysisInputProvided': True, 'unitInput': {'sourceBinding': {
        'sourceRevisionId': revision['id'], 'sectionId': section['id']}}}
    facts = [{'factKind': 'event', 'subjectKey': '甲', 'predicate': '打开', 'value': '红门', 'evidence': [{'excerpt': '甲打开红门。'}]}]
    assert "maxItems" not in tools["appendAnalysisFacts"].schema.parameters["properties"]["facts"]
    assert tools["appendAnalysisFacts"].max_argument_chars == 300000
    facts = [{**facts[0], "predicate": f"打开-{index}"} for index in range(16)]
    saved, result = await invoke(tools, state, 'appendAnalysisFacts', {'facts': facts}, 'facts-1')
    assert not saved.error_code and result['savedCount'] == 16
    _, replay = await invoke(tools, state, 'appendAnalysisFacts', {'facts': facts}, 'facts-1')
    assert replay == result
    mixed = {'facts': [facts[0], {**facts[0], 'evidence': [{'excerpt': '改写的错误引文'}]}]}
    partial, accepted = await invoke(tools, state, 'appendAnalysisFacts', mixed, 'mixed')
    assert partial.error_code is None
    assert accepted['status'] == 'partial' and accepted['savedCount'] == 1
    assert accepted['rejected'][0]['index'] == 1
    _, repeated = await invoke(tools, state, 'appendAnalysisFacts', mixed, 'mixed')
    assert repeated == accepted
    invalid, _ = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [{**facts[0], 'evidence': [{'excerpt': '不在来源中的句子'}]}, {**facts[0], 'evidence': [{'excerpt': '另一条错误引文'}]}]}, 'invalid')
    assert invalid.error_code == 'tool_input_invalid'
    assert 'facts[0].evidence[0]' in invalid.content
    assert 'facts[1].evidence[0]' in invalid.content
    assert '本批尚未保存' in invalid.content
    from purra.contracts import ToolEffectState
    assert invalid.effect_state == ToolEffectState.NOT_STARTED
    corrected, _ = await invoke(tools, state, 'appendAnalysisFacts', {'facts': facts}, 'corrected')
    assert corrected.error_code is None
    catalog = {tool.schema.name: tool for tool in build_novel_analysis_tool_catalog(db).registrations()}
    submitted = await catalog['submitNovelAnalysisResult'].handler(state, {'result': {'facts': [], 'craftCards': []}}, None)
    assert not submitted.error_code
    stored = await load_unit_model_result(db, state.run_id)
    assert len(stored['facts']) == 16 and stored['facts'][0]['value'] == '红门'
    assert stored['facts'][0]['evidence'][0]['sectionId'] == section['id']


async def test_generated_source_copy_is_rejected_before_draft_mutation(scope):
    _, service, state, tools = scope
    excerpt = "".join(chr(0x4e00 + i) for i in range(40))
    state.domain["unitInput"]["observations"][0]["evidence"] = [{"excerpt": excerpt}]
    result, _ = await invoke(tools, state, "applyTechniqueDraftChanges", {
        "expectedDraftRevision": 0,
        "changes": [{"action": "put", "path": "SKILL.md", "content": ENTRY + "\n" + excerpt}]}, "copy")
    assert result.error_code == "tool_input_invalid"
    ref = state.domain["unitInput"]["techniqueDraft"]
    assert service.techniques.get_draft(ref["techniqueId"], ref["draftId"])["draftRevision"] == 0
    fixed, draft = await invoke(tools, state, "applyTechniqueDraftChanges", {
        "expectedDraftRevision": 0,
        "changes": [{"action": "put", "path": "SKILL.md", "content": ENTRY}]}, "original")
    assert not fixed.error_code
    assert draft["draftRevision"] == 1


@pytest.mark.parametrize('multiple', [False, True])
async def test_generated_files_seal_idempotently_and_remain_unpublished(scope, multiple):
    db, service, state, tools = scope
    changes = [{'action': 'put', 'path': 'SKILL.md', 'content': ENTRY + ('\n[展开](节奏.md)' if multiple else '')}]
    if multiple:
        changes.append({'action': 'put', 'path': '节奏.md', 'content': '辅助技法。'})
    outcome, draft = await invoke(tools, state, 'applyTechniqueDraftChanges', {'expectedDraftRevision': 0, 'changes': changes}, 'files')
    assert not outcome.error_code
    _, repeated = await invoke(tools, state, 'applyTechniqueDraftChanges', {'expectedDraftRevision': 0, 'changes': changes}, 'files')
    assert repeated == draft
    request = {'result': {'status': 'generated', 'expectedDraftRevision': draft['draftRevision'], 'expectedTreeDigest': draft['treeDigest'],
        'evidenceRefs': ['wrong'], 'scopeNotes': [], 'reason': ''}}
    failed, _ = await invoke(tools, state, 'submitWritingTechnique', request, 'wrong-evidence')
    assert failed.error_code == 'tool_input_invalid'
    assert service.techniques.get_draft(draft['techniqueId'], draft['draftId'])['state'] == 'editing'
    _, listed = await invoke(tools, state, 'listAnalysisObservations', {}, 'observation-list')
    request['result']['evidenceRefs'] = [listed['items'][0]['id']]
    assert len(listed['items'][0]['id']) < 16
    outcome, receipt = await invoke(tools, state, 'submitWritingTechnique', request, 'submit')
    assert not outcome.error_code
    _, repeated = await invoke(tools, state, 'submitWritingTechnique', request, 'submit')
    assert receipt == repeated
    result = await load_unit_model_result(db, 'unit')
    assert result['techniqueResult']['evidenceRefs'] == ['observation']
    ref = result['techniqueResult']['candidate']
    assert ref['versionId'] == draft['treeDigest']
    assert not await service.list_objects('technique')
    record = service.techniques.get_record(ref['techniqueId'])
    assert record['publishedHead'] is None
    manifest = service.techniques.get_version_manifest({'kind': 'technique', 'id': ref['techniqueId'], 'versionId': ref['versionId']}, verify_files=True)
    assert len(manifest['files']) == 1 + int(multiple)
    await db.execute("UPDATE ai_agent_runs SET status='canceled' WHERE id='unit'")
    outcome, _ = await invoke(tools, state, 'readTechniqueDraftFile', {'draftRevision': draft['draftRevision'], 'path': 'SKILL.md'}, 'read-after-cancel')
    assert outcome.error_code == 'authorization_revoked'


async def test_insufficient_material_is_a_saved_result_without_sealing_empty_files(scope):
    db, service, state, tools = scope
    request = {'result': {'status': 'insufficient_material', 'expectedDraftRevision': 0, 'expectedTreeDigest': '',
        'evidenceRefs': [], 'scopeNotes': [], 'reason': '只有一句背景陈述，没有足够的写作机制依据。'}}
    outcome, _ = await invoke(tools, state, 'submitWritingTechnique', request, 'absent')
    assert not outcome.error_code
    result = await load_unit_model_result(db, 'unit')
    assert result['techniqueResult']['candidate'] is None
    draft = state.domain['unitInput']['techniqueDraft']
    assert service.techniques.get_draft(draft['techniqueId'], draft['draftId'])['state'] == 'editing'


async def test_evidence_reader_is_bound_to_frozen_input_and_revoked_runs(scope):
    from application.analysis_evidence_references import evidence_index
    db, _, state, tools = scope
    state.domain['unitInput']['sourceRevisionId'] = 'source'
    state.domain['unitInput']['observations'][0]['evidence'] = [{'sectionId': 's', 'excerpt': '脚步停住了。'}]
    key = next(iter(evidence_index(state.domain['unitInput'])))
    outcome, result = await invoke(tools, state, 'readAnalysisEvidence', {'ids': [key]}, 'evidence')
    assert not outcome.error_code
    assert result['evidence'][0]['excerpt'] == '脚步停住了。'
    outcome, _ = await invoke(tools, state, 'readAnalysisEvidence', {'ids': ['foreign']}, 'foreign')
    assert outcome.error_code == 'tool_input_invalid'
    assert 'listAnalysisEvidence' in outcome.content
    listed, directory = await invoke(tools, state, 'listAnalysisEvidence', {'offset': 0, 'limit': 1}, 'directory')
    assert not listed.error_code
    short = directory['items'][0]['evidenceId']
    assert len(short) < 16
    read, content = await invoke(tools, state, 'readAnalysisEvidence', {'ids': [short]}, 'short')
    assert not read.error_code
    assert content['evidence'][0]['excerpt'] == '脚步停住了。'
    await db.execute("UPDATE ai_agent_runs SET status='canceled' WHERE id='unit'")
    outcome, _ = await invoke(tools, state, 'readAnalysisEvidence', {'ids': [key]}, 'revoked')
    assert outcome.error_code == 'authorization_revoked'


def test_observation_paging_keeps_all_unmerged_mechanisms():
    observations = [{'cardKind': str(i), 'title': str(i), 'bodyMarkdown': '独立机制 ' + str(i), 'evidence': []} for i in range(80)]
    payload = {'sectionCandidates': [{'craftCards': observations, 'facts': []}]}
    ids = unit_observations(payload)
    projected = json.loads(analysis_unit_input_text(payload))
    assert projected['observationCount'] == 80
    assert 'craftCards' not in projected['sectionCandidates'][0]
    merged = {'cardKind': '合并', 'title': '合并机制', 'bodyMarkdown': '机制归并', 'evidence': [], 'mergedObservationIds': [ids[0]['contentDigest'], ids[1]['contentDigest']]}
    retained = _preserve_observations({'facts': [], 'craftCards': [merged]}, payload['sectionCandidates'])
    assert len(retained['craftCards']) == 79
    assert any(item['title'] == '79' for item in retained['craftCards'])
    with pytest.raises(ValueError):
        _preserve_observations({'craftCards': [{**merged, 'mergedObservationIds': ['foreign']}]}, payload['sectionCandidates'])


async def test_source_read_stops_at_eof_and_preserves_bound_scope(scope):
    from tests.test_novel_analysis import _source
    db, _, state, tools = scope
    revision = await _source(db)
    section = revision['sections'][0]
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('source-reader','running','novel_source_analysis.unit',?,?)", [revision['id'], json.dumps({'unitId': 'skill:draft'})])
    state.run_id = 'source-reader'
    state.domain['unitInput']['sectionIds'] = [section['id']]
    outcome, result = await invoke(tools, state, 'readTechniqueSource', {'sectionId': section['id'], 'startCharacter': 0, 'endCharacter': 1000}, 'read-eof')
    assert not outcome.error_code
    assert result['endCharacter'] == result['totalCharacters'] == len(result['text'])
    outcome, _ = await invoke(tools, state, 'readTechniqueSource', {'sectionId': revision['sections'][1]['id'], 'startCharacter': 0, 'endCharacter': 20}, 'read-unbound')
    assert outcome.error_code == 'invalid_reference'
    outcome, _ = await invoke(tools, state, 'readTechniqueSource', {'sectionId': section['id'], 'startCharacter': 0, 'endCharacter': 24001}, 'read-over-budget')
    assert outcome.error_code == 'tool_input_invalid'


@pytest.mark.parametrize('has_observations', [False, True])
def test_analysis_observation_tools_require_frozen_observations(has_observations):
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog
    from domains.novel_analysis import NovelAnalysisDomainContext

    payload = {'sourceBinding': {'sectionId': 's'}}
    if has_observations:
        payload['sectionCandidates'] = [{'craftCards': [{
            'title': '延迟揭示', 'cardKind': '节奏', 'bodyMarkdown': '先行动后解释',
        }]}]
    context = NovelAnalysisDomainContext(
        source_revision_id='revision', command_id='command', section_ids=('s',),
        interaction_kind='unit', unit_input=payload,
    )
    catalog = build_novel_analysis_tool_catalog(None)
    names = catalog.enabled_names(SimpleNamespace(domain_context=context.to_core_context()))
    assert {'submitNovelAnalysisResult', 'appendAnalysisObservations'} <= names
    assert ('listAnalysisObservations' in names) is has_observations
    assert ('readAnalysisObservations' in names) is has_observations
    projected = json.loads(analysis_unit_input_text(payload))
    assert projected['observationCount'] == int(has_observations)
    assert projected['sourceBinding'] == payload['sourceBinding']


async def test_observation_read_attaches_context_without_rewriting_frozen_observation(scope, monkeypatch):
    from copy import deepcopy
    from application.novel_analysis_source import NovelAnalysisSourceReader
    _, _, state, tools = scope
    observation = {'contentDigest': 'observation', 'cardKind': '场景', 'title': '切换',
        'bodyMarkdown': '顾遥“屏住气”。', 'evidence': [{'sectionId': 's', 'excerpt': '她觉得羞愧。'}]}
    before = deepcopy(observation)
    state.domain['unitInput'].update(observations=[observation], sectionIds=['s'])
    calls = []
    async def read(self, **kwargs):
        calls.append(kwargs)
        return {'text': '她觉得羞愧。另一边，岑野屏住气。'}
    monkeypatch.setattr(NovelAnalysisSourceReader, 'read_section', read)
    outcome, result = await invoke(tools, state, 'readAnalysisObservations', {'ids': ['observation']}, 'grounding')
    assert not outcome.error_code
    item = result['observations'][0]
    assert item['observationId'].startswith('O') and 'contentDigest' not in item
    assert {key: value for key, value in item.items() if key != 'observationId'} == {key: value for key, value in before.items() if key != 'contentDigest'}
    assert observation == before
    assert result['grounding'][0]['observationId'] == item['observationId']
    assert result['grounding'][0]['quotesOutsideEvidence'] == ['屏住气']
    assert result['grounding'][0]['contexts'][0]['text'].endswith('岑野屏住气。')
    assert result['validationScope'] == 'literal_evidence_only'
    assert calls == [{'source_revision_id': 'source', 'bound_section_ids': ['s'], 'section_id': 's'}]


def test_evidence_input_failure_uses_bounded_runtime_recovery():
    from purra.contracts import ToolBatchResult, ToolBatchOutcome, ToolCallResult, ToolEffectState
    from purra.recovery import RecoveryLedger
    from purra.runtime.tool_recovery import resolve_tool_recovery, ToolRecoveryDisposition
    ledger = RecoveryLedger()
    def recover(code):
        return resolve_tool_recovery(
            ToolBatchResult(results=(ToolCallResult(tool_call_id='bad-evidence', tool_name='appendAnalysisFacts',
                content='修正摘录后重新提交', error=code),), outcome=ToolBatchOutcome.FAILED,
                error=code, effect_state=ToolEffectState.NOT_STARTED),
            requested_names={'appendAnalysisFacts'}, planning_available=False,
            recovery_ledger=ledger, input_recovery_epoch=0, remaining_model_rounds=4,
            round_number=1, cancellation_requested=False)
    assert recover('tool_input_invalid').disposition == ToolRecoveryDisposition.RETRY_MODEL
    assert recover('authorization_revoked').disposition == ToolRecoveryDisposition.REJECT
    outcomes = [recover('tool_input_invalid').disposition for _ in range(10)]
    assert outcomes[-1] == ToolRecoveryDisposition.REJECT


def test_analysis_input_recovery_budget_is_bounded_and_preserves_other_limits():
    from application.novel_analysis_agent_profile import NovelAnalysisDomainAdapter
    from purra.recovery import RecoveryPolicy
    from purra.recovery.contracts import RecoveryCause
    policy = NovelAnalysisDomainAdapter().recovery_policy
    assert policy.max_attempts(RecoveryCause.TOOL_INPUT_INVALID) == 3
    default = RecoveryPolicy()
    for cause in RecoveryCause:
        if cause != RecoveryCause.TOOL_INPUT_INVALID:
            assert policy.max_attempts(cause) == default.max_attempts(cause)


async def test_fact_scope_is_host_owned_and_invalid_items_are_recoverable(scope):
    from tests.test_continuations import _published_analysis
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('scope-unit','running','novel_source_analysis.unit',?,'{}')", [revision['id']])
    source = await db.fetch_one('SELECT text_content FROM novel_source_sections WHERE id=?', [section['id']])
    end = len(source['text_content'])
    state.run_id = 'scope-unit'
    state.domain = {'sourceRevisionId': revision['id'], 'sectionIds': [section['id']], 'interactionKind': 'unit', 'analysisInputProvided': True, 'unitInput': {'sourceBinding': {
        'sourceRevisionId': revision['id'], 'sectionId': section['id'], 'segmentId': 'bound-segment',
        'startCharacter': 0, 'endCharacter': end}}}
    fact = {'factKind': 'event', 'subjectKey': '甲', 'predicate': '打开', 'value': '红门',
        'evidence': [{'excerpt': '甲打开红门。', 'segmentId': section['id'], 'segmentStartCharacter': 0}]}
    saved, result = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [fact]}, 'incomplete-range')
    assert saved.error_code is None and result['savedCount'] == 1
    from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
    page = await NovelAnalysisArtifactStore(db).require(result['artifactRef'])
    evidence = page['facts'][0]['evidence'][0]
    assert evidence['segmentId'] == 'bound-segment'
    assert evidence['segmentEndCharacter'] == end
    invalid = {**fact, 'subjectKey': ' '}
    partial, result = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [fact, invalid]}, 'mixed-shape')
    assert partial.error_code is None and result['savedCount'] == 1
    assert result['rejected'][0]['index'] == 1
    failed, _ = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [invalid]}, 'bad-shape')
    assert failed.error_code == 'tool_input_invalid'
    wrong = {**fact, 'evidence': [{'excerpt': '甲打开红门。', 'sectionId': 'another-section'}]}
    denied, _ = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [wrong]}, 'wrong-section')
    assert denied.error_code == 'invalid_reference'


async def test_observation_typo_is_repairable_and_directory_handles_read_exactly(scope):
    db, _, state, tools = scope
    original = state.domain['unitInput']['observations'][0]
    digest = 'sha256:91c7e02df3ba847a1e7d8d6832b021b5690962c13c0e29b1c93c9652c6c288ad'
    original['contentDigest'] = digest
    bad = digest.replace('288ad', '28ad')
    # Reproduce the actual incident: one missing character must not become a fatal reference error.
    failed, _ = await invoke(tools, state, 'readAnalysisObservations', {'ids': [bad]}, 'bad-observation')
    assert failed.error_code == 'tool_input_invalid'
    assert 'listAnalysisObservations' in failed.content
    from purra.contracts import ToolEffectState
    assert failed.effect_state == ToolEffectState.NOT_STARTED
    _, directory = await invoke(tools, state, 'listAnalysisObservations', {'limit': 1}, 'list-observation')
    handle = directory['items'][0]['id']
    assert handle.startswith('O') and len(handle) < 16
    read, result = await invoke(tools, state, 'readAnalysisObservations', {'ids': [handle, handle]}, 'read-observation')
    assert not read.error_code and len(result['observations']) == 1
    assert result['observations'][0]['observationId'] == handle
    assert original['contentDigest'] == digest
    await db.execute("UPDATE ai_agent_runs SET status='canceled' WHERE id='unit'")
    denied, _ = await invoke(tools, state, 'readAnalysisObservations', {'ids': [handle]}, 'denied-observation')
    assert denied.error_code == 'authorization_revoked'


async def test_chapter_summary_batch_survives_final_submission(scope):
    from tests.test_continuations import _published_analysis
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('chapter-unit','running','novel_source_analysis.unit',?,'{}')", [revision['id']])
    state.run_id = 'chapter-unit' 
    state.domain = {'sourceRevisionId':revision['id'], 'sectionIds':[section['id']], 'interactionKind':'unit', 'analysisInputProvided':True,
        'unitInput':{'sourceBinding':{'sourceRevisionId':revision['id'], 'sectionId':section['id']}}}
    fact = {'factKind':'background','subjectKey':'环境','predicate':'背景','value':'背景归纳','claimNature':'summary',
        'evidence':[{'referenceKind':'chapter','sectionId':section['id']}]}
    saved, result = await invoke(tools,state,'appendAnalysisFacts',{'facts':[fact]},'chapter-batch')
    assert not saved.error_code and result['savedCount']==1
    submitted = await build_novel_analysis_tool_catalog(db).get('submitNovelAnalysisResult').handler(state,{'result':{'facts':[],'craftCards':[]}},None)
    assert not submitted.error_code, submitted.content
    stored = await load_unit_model_result(db,state.run_id)
    assert stored['facts'][0]['claimNature']=='summary'
    assert stored['facts'][0]['evidence'][0]['referenceKind']=='chapter'


async def test_mixed_reference_recovery_and_host_selected_excerpts(scope):
    from tests.test_continuations import _published_analysis
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog
    from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('picker-unit','running','novel_source_analysis.unit',?,'{}')",[revision['id']])
    state.run_id='picker-unit'
    state.domain={'sourceRevisionId':revision['id'],'sectionIds':[section['id']],'interactionKind':'unit','analysisInputProvided':True,
        'unitInput':{'sourceBinding':{'sourceRevisionId':revision['id'],'sectionId':section['id']}}}
    fact={'factKind':'event','subjectKey':'甲','predicate':'打开','value':'红门','evidence':[{'referenceKind':'chapter','sectionId':section['id'],'excerpt':'甲打开红门。'}]}
    saved, result = await invoke(tools,state,'appendAnalysisFacts',{'facts':[fact]},'mixed-real-shape')
    assert not saved.error_code
    stored=await NovelAnalysisArtifactStore(db).require(result['artifactRef'])
    assert stored['facts'][0]['evidence'][0].get('referenceKind') != 'chapter'
    invalid={**fact,'evidence':[{'referenceKind':'chapter','excerpt':'他打开红门。'}]}
    bad,_=await invoke(tools,state,'appendAnalysisFacts',{'facts':[invalid]},'rewritten-quote')
    assert bad.error_code=='tool_input_invalid'
    found, directory=await invoke(tools,state,'findAnalysisSourceEvidence',{'query':'红门'},'find')
    assert not found.error_code and directory['items']
    handle=directory['items'][0]['sourceSpanId']
    selected={**fact,'predicate':'再次核对','evidence':[{'sourceSpanId':handle}]}
    saved,_=await invoke(tools,state,'appendAnalysisFacts',{'facts':[selected]},'selected')
    assert not saved.error_code
    invalid={**selected,'evidence':[{'sourceSpanId':handle[:-1]+('0' if handle[-1]!='0' else '1')}]}
    bad,_=await invoke(tools,state,'appendAnalysisFacts',{'facts':[invalid]},'forged')
    assert bad.error_code=='tool_input_invalid'
    submitted=await build_novel_analysis_tool_catalog(db).get('submitNovelAnalysisResult').handler(state,{'result':{'facts':[selected],'craftCards':[]}},None)
    assert not submitted.error_code,submitted.content
    state.domain['unitInput']['sourceBinding']['startCharacter'] = 10000
    outside,_=await invoke(tools,state,'appendAnalysisFacts',{'facts':[selected]},'outside-span')
    assert outside.error_code=='tool_input_invalid'


async def test_input_already_contains_usable_source_spans_without_search(scope):
    from tests.test_continuations import _published_analysis
    from application.analysis_source_spans import expand_spans, find_spans
    from application.novel_analysis_tools import analysis_unit_input_text
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    raw = await db.fetch_one('SELECT text_content FROM novel_source_sections WHERE id=?',[section['id']])
    payload = {'sourceBinding':{'sourceRevisionId':revision['id'],'sectionId':section['id']},'sourceEvidence':{'text':raw['text_content']}}
    projected = json.loads(analysis_unit_input_text(payload))
    assert 'text' not in projected['sourceEvidence']
    assert payload['sourceEvidence']['text']==raw['text_content']
    state.domain={'sourceRevisionId':revision['id'],'unitInput':payload}
    excerpt=projected['sourceEvidence']['excerpts'][0]
    expanded=await expand_spans(db,state,{'facts':[{'evidence':[dict(excerpt, referenceKind='quote')]}]})
    assert expanded['facts'][0]['evidence'][0]['excerpt']==excerpt['excerpt']
    result=await find_spans(db,state,'甲 红门')
    assert result['items']


async def test_empty_extra_fields_are_ignored_but_meaningful_unknown_content_is_recoverable(scope):
    from tests.test_continuations import _published_analysis
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog
    from purra.tools.security import validate_tool_arguments_schema
    db, _, state, tools = scope
    revision = await _published_analysis(db)
    section = revision['sections'][0]
    raw = await db.fetch_one('SELECT text_content FROM novel_source_sections WHERE id=?', [section['id']])
    await db.execute("INSERT INTO ai_agent_runs(id,status,binding_namespace,binding_aggregate_id,binding_attributes_json) VALUES ('short-unit','running','novel_source_analysis.unit',?,'{}')", [revision['id']])
    state.run_id = 'short-unit'
    state.domain = {'sourceRevisionId': revision['id'], 'sectionIds': [section['id']], 'interactionKind': 'unit', 'analysisInputProvided': True,
        'unitInput': {'sourceBinding': {'sourceRevisionId': revision['id'], 'sectionId': section['id']}, 'sourceEvidence': {'text': raw['text_content']}}}
    fact = {'factKind': 'event', 'subjectKey': '甲', 'predicate': '开门', 'value': '红门', 'value_note': '', 'optional_label': None,
        'evidence': [{'sourceSpanId': 'S001'}]}
    parsed = SimpleNamespace(call=SimpleNamespace(name='appendAnalysisFacts'), arguments={'facts': [fact]})
    assert validate_tool_arguments_schema(parsed, tools['appendAnalysisFacts'].schema.parameters) is None
    bad = {**fact, 'value_note': '不能丢弃的额外分析'}
    saved, result = await invoke(tools, state, 'appendAnalysisFacts', {'facts': [fact, bad]}, 'empty-and-meaningful')
    assert not saved.error_code and result['savedCount'] == 1 and result['rejected'][0]['index'] == 1
    from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
    persisted = await NovelAnalysisArtifactStore(db).require(result['artifactRef'])
    assert 'value_note' not in persisted['facts'][0] and 'optional_label' not in persisted['facts'][0]
    assert 'sourceSpanId' not in persisted['facts'][0]['evidence'][0]
    submit = build_novel_analysis_tool_catalog(db).get('submitNovelAnalysisResult')
    rejected = await submit.handler(state, {'result': {'facts': [bad], 'craftCards': []}}, None)
    assert rejected.error_code == 'tool_input_invalid'
    with pytest.raises(ValueError, match='did not submit'):
        await load_unit_model_result(db, state.run_id)
    result = await submit.handler(state, {'result': {'facts': [fact], 'craftCards': []}}, None)
    assert not result.error_code, result.content
    stored = await load_unit_model_result(db, state.run_id)
    assert len(stored['facts']) == 1 and 'value_note' not in stored['facts'][0]
    _, directory = await invoke(tools, state, 'findAnalysisSourceEvidence', {}, 'list-short-ids')
    assert directory['items'][0]['sourceSpanId'] == 'S001'
    # Scope/lifecycle checks still happen before a read or candidate write.
    await db.execute("UPDATE ai_agent_runs SET status='canceled' WHERE id='short-unit'")
    denied, _ = await invoke(tools, state, 'findAnalysisSourceEvidence', {}, 'revoked-short-ids')
    assert denied.error_code == 'authorization_revoked'
