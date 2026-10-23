import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin' / 'LLMLINK' / 'llmlink_c4d'))
from protocol import MAX_MESSAGE, decode, encode, validate_step
from providers import SYSTEM, local_base


class ProtocolTests(unittest.TestCase):
    def test_fragmented_unicode_and_multiple_frames(self):
        first = {'type': 'chat', 'text': 'Создай сферу'}
        wire = encode(first)
        buffer = bytearray(wire[:5])
        self.assertEqual(decode(buffer), [])
        buffer.extend(wire[5:] + encode({'type': 'stop'}))
        self.assertEqual(decode(buffer, limit=1), [first])
        self.assertEqual(decode(buffer), [{'type': 'stop'}])
        self.assertFalse(buffer)

    def test_reject_oversized_or_non_object_frame(self):
        for buffer in (bytearray(struct.pack('!I', MAX_MESSAGE + 1)), bytearray(b'\0\0\0\x02[]')):
            with self.assertRaises(ValueError):decode(buffer)

    def test_no_unvalidated_model_code(self):
        for step in ({'action': 'shell', 'message': '', 'code': ''},
                     {'action': 'python', 'message': '', 'code': 'for'},
                     {'action': 'done', 'message': '', 'code': 'print(1)'}):
            with self.assertRaises((ValueError, SyntaxError)):validate_step(step)

    def test_only_local_studio_endpoint(self):
        self.assertEqual(local_base('http://127.0.0.1:1234/v1'), 'http://127.0.0.1:1234/v1')
        for url in ('https://example.org', 'http://localhost.evil.test', 'file:///tmp', 'http://user:pass@localhost'):
            with self.assertRaises(ValueError):local_base(url)

    def test_cinema_prompt_supports_rendering(self):
        self.assertIn('llmlink.render', SYSTEM)
        self.assertIn('Cinema 4D Python API', SYSTEM)
        self.assertNotIn('Do not run renders', SYSTEM)

