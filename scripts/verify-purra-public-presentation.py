"""Live final-presentation probe using only the repository's synthetic E02 source.

This invokes the installed PurrA transaction and the host Provider gateway. It is
not an end-to-end novel analysis and creates no production Artifact or Run.
"""
import argparse
import asyncio
import importlib.util
import json
from importlib.metadata import version
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
spec = importlib.util.spec_from_file_location('technique_eval', ROOT / 'scripts/evaluate-writing-techniques.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
from application.model_runtime import model_request_from_runtime, reasoning_mode_from_options
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from purra.contracts import AgentRunResult, RunStatus
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.output import PublicFact, PublicFactBundle, PublicPresentationMode, ResponseTransactionMode, ResponseTransactionPolicy
from purra.output.response_transaction import AgentResponseTransaction
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


async def main(args):
    if args.output.exists():
        raise ValueError('Use a new output file')
    config = evaluation.configured_model(args.settings_db, args.model)
    cases, sources = evaluation.fixtures()
    case = next(item for item in cases['cases'] if item['id'] == 'E02')
    source = sources[case['generatorInput']['sourceIds'][0]]['text']
    class Facts:
        async def facts_for(self, run_id, result):
            return PublicFactBundle(facts=(PublicFact('testMaterial', source), PublicFact('testPurpose', '这是推理配置复测中的虚构测试材料，未执行真实作品分析或保存操作。')),)
    report = {'purraVersion': version('purra'), 'model': args.model, 'results': []}
    for mode in ['enabled', 'disabled']:
        runtime = ScreenplayAgentRuntimeRequest(apiKey=config['apiKey'], baseURL=config['baseUrl'], apiProvider=config['apiProvider'],
            options={'model': config['name'], 'model_profile': config['presetId'], 'profile_binding': 'compatible',
                'thinking': {'type': mode}, 'max_generation_tokens': 4096}, contextWindow=config['contextWindow'])
        manager = AgentModelInvocationManager(ProviderModelGateway(config['apiKey']))
        transaction = AgentResponseTransaction(manager, policy=ResponseTransactionPolicy(
            mode=ResponseTransactionMode.VALIDATED_RESULT, public_presentation=PublicPresentationMode.MODEL_LIVE),
            facts_provider=Facts(), max_presentation_attempts=1)
        context = ModelInvocationContext(run_id='presentation-probe-'+mode, requested_reasoning_mode=reasoning_mode_from_options(runtime.options))
        try:
            text = await asyncio.wait_for(transaction.present(AgentRunResult(run_id=context.run_id, status=RunStatus.DONE, final_response=''),
                request=model_request_from_runtime(runtime), context=context), 240)
            report['results'].append({'mode': mode, 'status': 'passed', 'text': text})
        except Exception as error:
            report['results'].append({'mode': mode, 'status': 'failed', 'errorType': type(error).__name__, 'errorCode': getattr(error, 'code', '')})
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(mode, report['results'][-1]['status'], flush=True)
    return int(any(row['status'] != 'passed' for row in report['results']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='kimi-k2.6')
    parser.add_argument('--settings-db', type=Path, default=Path.home() / 'Library/Application Support/purrtypos/purrtypos.db')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
