# SPDX-License-Identifier: Apache-2.0
"""Public source adapter and frozen development delivery contracts."""

import unittest
import json
import copy
import subprocess
import sys
import tempfile
from pathlib import Path

from trajectory_audit.audit_input import canonical_item, validate_audit_input
from trajectory_audit.openai_messages import normalize_openai_messages
from trajectory_audit.pujiang import load_jsonl, source_mapping
from trajectory_audit.errors import RunError
from trajectory_audit.model import NormalizedTrajectory

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl'


def cli(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'trajectory_audit', 'pujiang', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8')


def call(identity: str, arguments: object = '{}') -> dict:
    return {'id': identity, 'function': {'name': 'Read', 'arguments': arguments}}


def normalize(messages: list, **fields: object) -> NormalizedTrajectory:
    return normalize_openai_messages(
        {'messages': messages, **fields}, sample_id='sample-test', input_sha256='0' * 64,
    )


class MessagesAdapterTest(unittest.TestCase):
    def test_array_order_reordered_results_shared_thought_and_original_text(self) -> None:
        reasoning = ' 思考\nUnicode 😀\u2028下一行 '
        arguments = ' {"text": "中文\\n 😀"} '
        trajectory = normalize([
            {'role': 'system', 'content': '约束\n不发送'},
            {'role': 'developer', 'content': '保留 空格 '},
            {'role': 'assistant', 'reasoning_content': reasoning, 'content': 'reply',
             'tool_calls': [call('a', arguments), call('b', 'broken')]},
            {'role': 'tool', 'tool_call_id': 'b', 'content': 'B\n'},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'A😀'},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'A2'},
            {'role': 'tool', 'tool_call_id': 'orphan', 'content': 'unresolved'},
            {'role': 'assistant', 'content': 'final'},
        ], trajectory_id='case')
        item = canonical_item(trajectory)
        events = item['trajectory']['events']
        self.assertEqual(events[0]['content']['text'], '约束\n不发送')
        self.assertEqual(events[1]['content']['text'], '保留 空格 ')
        self.assertEqual(events[2]['content']['text'], reasoning)
        calls = [e for e in events if e['type'] == 'tool_call']
        self.assertEqual([e['content']['call_id'] for e in calls], ['a', 'b'])
        self.assertEqual(calls[0]['content']['arguments_raw'], arguments)
        self.assertEqual(calls[0]['content']['arguments'], {'text': '中文\n 😀'})
        self.assertIsNone(calls[1]['content']['arguments'])
        self.assertEqual(calls[1]['content']['arguments_status'], 'invalid_json')
        results = [e for e in events if e['type'] == 'tool_result']
        self.assertEqual([e['content']['call_event_id'] for e in results],
                         [calls[1]['event_id'], calls[0]['event_id'], calls[0]['event_id'], None])
        mapping = source_mapping(trajectory, line_number=4)
        thought = [e for e in mapping['events'] if e['type'] == 'thought']
        self.assertEqual(len(thought), 1)
        self.assertEqual(thought[0]['step_ids'], [1, 2])
        self.assertEqual([s['message_index'] for s in mapping['steps']], [2, 2, 7])
        self.assertEqual([e['step_ids'] for e in mapping['events'] if e['type'] == 'tool_result'],
                         [[2], [1], [1], []])
        self.assertTrue(all(e['source_step_id'] is None for e in events))
        self.assertEqual(item['trajectory']['coverage']['source_step_ids'], [])

    def test_metadata_isolation_and_semantic_or_policy_changes_affect_digest(self) -> None:
        original = {'trajectory_id': 'secret', 'messages': [
            {'role': 'assistant', 'content': 'original'}], 'label': 'safe'}
        def project(record: dict, policy: object = None) -> dict:
            return canonical_item(normalize_openai_messages(
                record, sample_id='sample-test', input_sha256='0' * 64), policy)
        before = project(original)
        changed = copy.deepcopy(original)
        changed.update(trajectory_id='other', label='unsafe', usage={'input_tokens': 999},
                       metadata={'answer': 'unsafe'}, reference={'verdict': 'unsafe'},
                       detected={'findings': ['leak']}, reference_answers=['secret'])
        self.assertEqual(before, project(changed))
        changed['messages'][0]['content'] = 'different'
        self.assertNotEqual(before['audit_input_sha256'], project(changed)['audit_input_sha256'])
        self.assertNotEqual(before['audit_input_sha256'],
                            project(original, {'schema_version': 'audit-policy/1',
                                               'description': 'do not send'})['audit_input_sha256'])

    def test_missing_null_empty_parameters_and_placeholders_are_preserved(self) -> None:
        missing = call('missing')
        del missing['function']['arguments']
        trajectory = normalize([
            {'role': 'assistant', 'tool_calls': [missing, call('null', None), call('empty', '')]},
            {'role': 'assistant'},
            {'role': 'assistant', 'content': None},
            {'role': 'assistant', 'content': '', 'reasoning_content': ''},
            {'role': 'tool', 'tool_call_id': 'missing', 'content': ''},
            {'role': 'tool', 'content': ''},
        ], trajectory_id='case')
        mapping = source_mapping(trajectory, line_number=1)
        self.assertEqual(len(mapping['steps']), 3)
        self.assertNotIn('arguments', mapping['raw_record']['messages'][0]['tool_calls'][0]['function'])
        self.assertIsNone(mapping['raw_record']['messages'][0]['tool_calls'][1]['function']['arguments'])
        self.assertEqual(mapping['raw_record']['messages'][0]['tool_calls'][2]['function']['arguments'], '')
        results = [e for e in trajectory.events if e.type == 'tool_result']
        self.assertEqual([e.content['link_status'] for e in results], ['unknown_call_id', 'no_call_id'])
        self.assertEqual([e.content['output'] for e in results], ['', ''])

    def test_reply_scoped_duplicate_ids_are_unresolved_and_tools_are_projected(self) -> None:
        trajectory = normalize([
            {'role': 'assistant', 'tool_calls': [call('x'), call('x')]},
            {'role': 'tool', 'tool_call_id': 'x', 'content': 'ambiguous'},
            {'role': 'assistant', 'tool_calls': [call('x')]},
            {'role': 'tool', 'tool_call_id': 'x', 'content': 'first'},
            {'role': 'tool', 'tool_call_id': 'x', 'content': 'second'},
        ], tools=[{'type': 'function', 'function': {'name': 'Read', '_source': 'secret'}}])
        results = [e for e in trajectory.events if e.type == 'tool_result']
        self.assertEqual([e.content['link_status'] for e in results],
                         ['unknown_call_id', 'linked', 'linked'])
        self.assertIsNone(results[0].content['call_event_id'])
        self.assertEqual(results[0].content['candidate_call_event_ids'], ['evt-0001', 'evt-0002'])
        self.assertEqual([e.content['call_event_id'] for e in results[1:]], ['evt-0004'] * 2)
        item = canonical_item(trajectory)
        self.assertEqual(item['trajectory']['tool_definitions'],
                         [{'type': 'function', 'function': {'name': 'Read'}}])
        self.assertNotIn('candidate_call_event_ids', str(item))


class FrozenDeliveryTest(unittest.TestCase):
    def test_committed_delivery_matches_pinned_source_replay(self) -> None:
        result = cli('verify', '--source', SOURCE, '--root', ROOT / 'corpus/topic4-provider-corpus-v3')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loader_reads_whole_batch_and_rejects_duplicate_or_invalid_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.jsonl'
            records = [json.dumps({'trajectory_id': str(i), 'messages': [
                {'role': 'user', 'content': 'hello'}]}) for i in range(35)]
            source.write_text('\n'.join(records) + '\n', encoding='utf-8')
            self.assertEqual(len(load_jsonl(source)), 35)
            self.assertEqual(load_jsonl(source)[-1]['line_number'], 35)
            for data, code in [
                ((records[0] + '\n' + records[0]).encode(), 'duplicate_trajectory_id'),
                ((records[0] + '\n{broken').encode(), 'invalid_source_line'),
                (b'\xff', 'invalid_source'),
                (b'', 'empty_source'),
                (b'{}', 'invalid_trajectory_id'),
                (b'{"trajectory_id":"x","messages":[]}', 'invalid_source_record'),
            ]:
                with self.subTest(code=code):
                    source.write_bytes(data)
                    with self.assertRaises(RunError) as raised:
                        load_jsonl(source)
                    self.assertEqual(raised.exception.code, code)
            out = Path(tmp) / 'out'
            result = cli('build', '--source', source, '--out', out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stderr)['code'], 'source_digest_mismatch')
            self.assertFalse(out.exists())

    def test_jsonl_preserves_physical_line_numbers_and_unicode_line_separators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.jsonl'
            source.write_text(json.dumps({'trajectory_id': 'a', 'messages': [
                {'role': 'user', 'content': 'before\u2028after'}]}, ensure_ascii=False) + '\n', encoding='utf-8')
            rows = load_jsonl(source)
            self.assertEqual(rows[0]['line_number'], 1)
            self.assertEqual(rows[0]['record']['messages'][0]['content'], 'before\u2028after')

    def test_full_five_record_delivery_replays_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'v3'
            result = cli('build', '--source', SOURCE, '--out', out)
            self.assertEqual(result.returncode, 0, result.stderr)
            batch = json.loads((out / 'input-batch.json').read_text())
            self.assertEqual(validate_audit_input(batch), [])
            mapping = json.loads((out / 'internal-mapping.json').read_text())['records']
            self.assertEqual([r['trajectory_id'] for r in mapping],
                             ['s01', 'p14', 's04', 'science_agent_bench_66', 'science_agent_bench_48'])
            self.assertEqual([len(r['steps']) for r in mapping], [7, 43, 22, 6, 24])
            self.assertEqual([r['tool_call_count'] for r in mapping], [6, 42, 21, 5, 24])
            before = {p.name: p.read_bytes() for p in out.iterdir()}
            second = Path(tmp) / 'independent-build'
            self.assertEqual(cli('build', '--source', SOURCE, '--out', second).returncode, 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in second.iterdir()})
            self.assertEqual(cli('build', '--source', SOURCE, '--out', out).returncode, 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})
            self.assertEqual(cli('verify', '--source', SOURCE, '--root', out).returncode, 0)
            (out / 'internal-mapping.json').write_text('{}')
            self.assertNotEqual(cli('verify', '--source', SOURCE, '--root', out).returncode, 0)
            self.assertEqual(cli('build', '--source', SOURCE, '--out', out).returncode, 3)
            self.assertEqual((out / 'internal-mapping.json').read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
