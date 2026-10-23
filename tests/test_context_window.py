import copy
import json
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'plugin/LLMLINK/llmlink_c4d'))
from context_window import ContextWindow, packed, project, recall
from providers import Providers, Cancelled
from protocol import validate_step
from companion import Session
from chat_store import ChatStore


def long_history(count=1000):
    result = [{'role': 'user', 'content': 'Constraint: keep the roof red; facade name AmberHouse.'}]
    for n in range(count):
        result.extend([
            {'role': 'user', 'content': 'Task %d: preserve roof; refine detail. ' % n + 'request ' * 35},
            {'role': 'assistant', 'content': {'action': 'python', 'message': 'Proposed detail %d' % n, 'code': 'print("detail")\n'*50}},
            {'role': 'tool', 'content': {'operation': 'python', 'ok': n % 5 != 0,
                                      'output': 'detail %d measured' % n, 'error': 'partial failure' if n % 5 == 0 else '',
                                      'scene': {'document': 'same-doc', 'revision': str(n), 'object_count': n,
                                                'objects': [{'name': 'AmberHouse', 'location': [1.123456, 0, 0]}]}}}])
    result.append({'role': 'user', 'content': 'CURRENT: change the lamp only, keep the roof red'})
    return result


class ContextWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = ContextWindow(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_thousand_requests_compact_and_reopen_without_losing_archive(self):
        history = long_history()
        before = packed(history)
        chunks = []
        def summarize(memory, chunk):
            chunks.extend(chunk)
            return 'Keep roof red. AmberHouse. Proposed and failed work must be verified in the current scene.'
        prepared = self.window.prepare(history, summarize, lambda: None)
        self.assertLessEqual(len(packed(prepared)), 110000)
        self.assertEqual(packed(history), before)
        self.assertEqual(prepared[-1], history[-1])
        self.assertIn('Keep roof red', packed(prepared))
        self.assertGreater(len(chunks), 2900)
        latest = next(item['content']['scene'] for item in reversed(prepared) if isinstance(item.get('content'), dict) and 'scene' in item['content'])
        self.assertEqual(latest['document'], 'same-doc')
        self.assertEqual(latest['revision'], '999')
        reopened = ContextWindow(self.temp.name).prepare(history, lambda *_: self.fail('Memory was not reused'), lambda: None)
        self.assertEqual(reopened, prepared)
        cache = next((Path(self.temp.name)/'context-memory').glob('*.json'))
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)

    def test_appended_messages_only_summarize_new_prefix(self):
        history = long_history(300)
        self.window.prepare(history, lambda *_: 'Red roof, AmberHouse', lambda: None)
        history.extend(long_history(50)[1:])
        chunks = []
        self.window.prepare(history, lambda previous, chunk: chunks.extend(chunk) or previous, lambda: None)
        self.assertTrue(chunks)
        self.assertLess(len(chunks), 200)

    def test_changed_or_corrupt_cache_is_not_used(self):
        history = long_history(300)
        self.window.prepare(history, lambda *_: 'Original memory', lambda: None)
        history[1]['content'] = 'User correction: roof must be blue' + ' detail'*100
        calls = []
        self.window.prepare(history, lambda memory, chunk: calls.append(memory) or 'Updated blue roof', lambda: None)
        self.assertEqual(calls[0], '')
        cache = next((Path(self.temp.name)/'context-memory').glob('*.json'))
        cache.write_text('{bad json')
        calls.clear()
        self.window.prepare(history, lambda memory, chunk: calls.append(memory) or 'Recovered', lambda: None)
        self.assertEqual(calls[0], '')

    def test_oversized_summary_does_not_replace_memory(self):
        history = long_history(300)
        with self.assertRaisesRegex(RuntimeError, 'memory'):
            self.window.prepare(history, lambda *_: 'x'*12001, lambda: None)
        self.assertFalse((Path(self.temp.name)/'context-memory').exists())

    def test_cancel_before_summarizing_has_no_side_effects(self):
        def cancel():
            raise Cancelled()
        with self.assertRaises(Cancelled):
            self.window.prepare(long_history(300), lambda *_: self.fail('cancel ignored'), cancel)
        self.assertFalse((Path(self.temp.name)/'context-memory').exists())

    def test_parameter_dump_and_old_code_do_not_fill_working_context(self):
        history = [
            {'role': 'user', 'content': 'Create a sky'},
            {'role': 'assistant', 'content': {'action': 'python', 'code': 'print(1)\n'*5000, 'message': 'Proposed sky'}},
            {'role': 'tool', 'content': {'ok': True, 'output': 'SKY_PARAMETER\n'*50000,
                                      'scene': {'document': 'doc', 'revision': 'precise', 'objects': [{'name': 'Part'+str(n), 'location': [0,0,0]} for n in range(300)]}}},
            {'role': 'user', 'content': 'Now create water'}]
        before = copy.deepcopy(history)
        prepared = self.window.prepare(history, lambda *_: self.fail('No summary needed'), lambda: None)
        self.assertLess(len(packed(prepared)), 35000)
        self.assertEqual(history, before)
        self.assertIn('full content', prepared[2]['content']['output'])

    def test_active_request_and_latest_repair_code_stay_exact(self):
        history = long_history(300)
        code = 'x = 1\n'*6000
        history.extend([
            {'role': 'assistant', 'content': {'action': 'python', 'message': 'Proposed, not successful', 'code': code}},
            {'role': 'tool', 'content': {'ok': False, 'error': 'AttributeError: water layer', 'error_line': 6,
                                      'scene': {'document': 'D', 'revision': 'R', 'objects': []}}}])
        prepared = self.window.prepare(history, lambda *_: 'Preserve roof; last Python failed', lambda: None)
        self.assertIn(history[-3], prepared)
        self.assertEqual(prepared[-2]['content']['code'], code)
        self.assertFalse(prepared[-1]['content']['ok'])

    def test_archive_recall_finds_early_constraint_beyond_recent_tail(self):
        history = long_history()
        result = recall(history, 'Constraint AmberHouse')
        self.assertTrue(result['ok'])
        self.assertTrue(any(item['record'] == 0 for item in result['matches']))
        self.assertLess(len(packed(result)), 9000)
        self.assertEqual(recall(history, 'doesnotexist')['matches'], [])

    def test_large_valid_archive_can_still_be_reopened(self):
        store = ChatStore(self.temp.name)
        ident = 'a'*32
        path = store.root/(ident+'.json')
        with path.open('w', encoding='utf-8') as stream:
            for _ in range(1025):
                stream.write(' '*65536)
            json.dump({'history':[{'role':'user','content':'Keep all my old work'}], 'events':[], 'goal':None, 'draft':{}},stream)
        self.assertGreater(path.stat().st_size,64*1024*1024)
        self.assertEqual(store.load(ident)['history'][0]['content'],'Keep all my old work')

    def test_memory_response_cannot_run_python(self):
        p = Providers(self.temp.name, lambda *a, **k: None)
        p.codex = lambda *_: {'action': 'python', 'message': 'Do something', 'code': 'print(1)'}
        with self.assertRaisesRegex(RuntimeError, 'No scene action'):
            p.summarize({'provider': 'codex'}, '', [])

    def test_provider_continues_large_history_instead_of_new_chat_error(self):
        p = Providers(self.temp.name, lambda *a, **k: None)
        prompts = []
        def model(config, prompt):
            prompts.append(prompt)
            return {'action': 'done', 'message': 'Red roof and AmberHouse remembered', 'code': ''}
        p.codex = model
        result = p.step({'provider': 'codex'}, long_history())
        self.assertEqual(result['action'], 'done')
        self.assertGreater(len(prompts), 2)
        self.assertTrue(all(len(prompt) < 150000 for prompt in prompts))
        self.assertIn('CURRENT:', prompts[-1])

    def test_recall_step_is_read_only_and_returns_to_model(self):
        a, b = socket.socketpair()
        try:
            s = Session(a, 'x'*40, self.temp.name)
            s.history = [{'role': 'user', 'content': 'Original roof colour is burgundy'}]
            operations = []
            s.command = lambda op, *a, **k: operations.append(op) or {'ok': True, 'scene': {'document': 'doc', 'revision': 'r'}}
            steps = iter([{'action': 'recall', 'message': 'burgundy', 'code': ''}, {'action': 'done', 'message': 'Remembered', 'code': ''}])
            s.provider.step = lambda *_: next(steps)
            s.run(s.config)
            self.assertEqual(operations, ['inspect'])
            result = next(h['content'] for h in s.history if isinstance(h.get('content'), dict) and h['content'].get('operation') == 'recall')
            self.assertIn('burgundy', result['matches'][0]['excerpt'])
            self.assertEqual(validate_step({'action': 'recall', 'message': 'roof', 'code': ''})['action'], 'recall')
        finally:
            a.close(); b.close()


if __name__ == '__main__':
    unittest.main()
