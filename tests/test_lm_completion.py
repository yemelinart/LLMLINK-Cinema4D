"""Regressions for reasoning consuming the entire local-model output budget."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin' / 'LLMLINK' / 'llmlink_c4d'))
from providers import Providers, Cancelled

STEP = {'action': 'python', 'message': 'Create a sphere',
        'code': "bpy.ops.mesh.primitive_uv_sphere_add()"}


def frame(delta=None, finish=None, **fields):
    return ('data: ' + json.dumps({'choices': [{'delta': delta or {},
                                               'finish_reason': finish}], **fields}) + '\n\n').encode()


class CompletionTests(unittest.TestCase):
    def run_stream(self, frames, effort='xhigh'):
        self.requests = []
        self.timeouts = []
        self.events = []
        def lines(request, cancel, timeout=300):
            self.requests.append(json.loads(request.data))
            self.timeouts.append(timeout)
            yield from frames
        provider = Providers('/tmp', lambda kind, **data: self.events.append((kind, data)))
        with patch('providers.lm_lines', lines):
            return provider.lmstudio({'model': 'qwen/qwen3.8-27b', 'effort': effort}, 'A small step')

    def test_reasoning_exhaustion_reports_budget_not_unsupported_model(self):
        frames = [frame({'reasoning_content': 'planning, not executable'}),
                  frame(finish='length', usage={'completion_tokens': 4096}),
                  b'data: [DONE]\n\n']
        with self.assertRaisesRegex(RuntimeError, 'output limit.*4096') as error:
            self.run_stream(frames)
        self.assertNotIn('working tool support', str(error.exception))

    def test_length_rejects_even_a_parseable_python_step(self):
        frames = [frame({'content': json.dumps(STEP)}, finish='length'), b'data: [DONE]\n\n']
        with self.assertRaisesRegex(RuntimeError, 'output limit'):
            self.run_stream(frames)
        self.assertEqual(len(self.requests), 1, 'Do not silently repeat a slow generation')

    def test_eof_without_end_marker_does_not_execute_valid_prefix(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            self.run_stream([frame({'content': json.dumps(STEP)})])

    def test_finished_usage_only_frame_and_split_tool_call(self):
        payload = json.dumps(STEP)
        frames = [frame({'reasoning_content': 'planning'}),
                  frame({'tool_calls': [{'index': 0, 'function': {'name': 'cinema4d_step', 'arguments': payload[:12]}}]}),
                  frame({'tool_calls': [{'index': 0, 'function': {'arguments': payload[12:]}}]}, finish='tool_calls'),
                  b'data: {"choices":[],"usage":{"completion_tokens":5000}}\n\n',
                  b'data: [DONE]\n\n']
        self.assertEqual(self.run_stream(frames), STEP)
        self.assertGreaterEqual(self.requests[0]['max_tokens'], 32768)
        self.assertGreaterEqual(self.timeouts[0], 1800)
        self.assertNotIn('temperature', self.requests[0], 'Use the model sampling settings in LM Studio')
        self.assertEqual(self.requests[0]['reasoning_effort'], 'xhigh')

    def test_low_and_off_also_have_room_for_final_code(self):
        for effort in ('off', 'low', 'medium', '', 'on'):
            with self.subTest(effort=effort):
                self.assertEqual(self.run_stream([frame({'content': json.dumps(STEP)}, finish='stop'),
                                                  b'data: [DONE]\n\n'], effort), STEP)
                self.assertGreaterEqual(self.requests[0]['max_tokens'], 8192)
                self.assertGreaterEqual(self.timeouts[0], 600)

    def test_unexpected_function_is_never_treated_as_cinema4d_step(self):
        frames = [frame({'tool_calls': [{'index': 0, 'function': {'name': 'other_tool',
                                                                   'arguments': json.dumps(STEP)}}]}, finish='tool_calls'),
                  b'data: [DONE]\n\n']
        with self.assertRaisesRegex(RuntimeError, 'Unexpected.*tool'):
            self.run_stream(frames)

    def test_empty_final_response_is_actionable_without_claiming_model_incapable(self):
        with self.assertRaisesRegex(RuntimeError, 'Thinking') as error:
            self.run_stream([frame({'reasoning': 'planning'}, finish='stop'), b'data: [DONE]\n\n'])
        self.assertNotIn('working tool support', str(error.exception))

    def test_finish_without_done_marker_is_accepted(self):
        self.assertEqual(self.run_stream([frame({'content': json.dumps(STEP)}, finish='stop')]), STEP)

    def test_explicit_error_not_retried(self):
        with self.assertRaisesRegex(RuntimeError, 'model context full'):
            self.run_stream([b'data: {"error":{"message":"model context full"}}\n\n'])
        self.assertEqual(len(self.requests), 1)

    def test_cancel_does_not_retry_or_return_partial_python(self):
        provider = Providers('/tmp', lambda *args, **kwargs: None)
        def lines(*args, **kwargs):
            yield frame({'content': json.dumps(STEP)})
            provider.cancelled.set()
            yield b'data: [DONE]\n\n'
        with patch('providers.lm_lines', lines), self.assertRaises(Cancelled):
            provider.lmstudio({'model': 'fixture'}, 'A small step')


if __name__ == '__main__':
    unittest.main()
