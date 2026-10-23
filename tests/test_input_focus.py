"""Regression coverage for the native editor's focus-changing scroll operation."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'plugin/LLMLINK/llmlink_c4d/dialog.py'
# Run the production presenter without importing the host-only c4d module.
node = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'LLMLINKDialog')
method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'redraw_history')
ns = {'HISTORY': 110, 'COMPOSER': 111, 'THOUGHTS': 124}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), 'exec'), ns)


class NativeEditorDouble:
    redraw_history = ns['redraw_history']

    def __init__(self):
        self.events = [{'kind': 'user', 'text': 'First task'}]
        self.stream = {}
        self.dirty = True
        self.history_text = None
        self.active = 111
        self.draft = 'Следующее задание: добавь свет'
        self.selection = (21, 25)
        self.writes = []
        self.scrolls = []
        self.thoughts = False

    def GetBool(self, ident):
        return self.thoughts

    def IsActive(self, ident):
        return self.active == ident

    def SetString(self, ident, text):
        self.writes.append((ident, text))

    def SetMultiLinePos(self, ident, line, pos):
        # Reproduced on C4D 2024.2: this operation also takes keyboard focus.
        self.active = ident
        self.scrolls.append((ident, line, pos))


class InputFocusTests(unittest.TestCase):
    def test_streaming_visible_thoughts_preserves_composer_and_selection(self):
        p = NativeEditorDouble()
        p.thoughts = True
        for n in range(100):
            p.stream['reasoning'] = 'Thinking ' + str(n)
            p.redraw_history()
            self.assertEqual(p.active, 111)
            self.assertEqual(p.draft, 'Следующее задание: добавь свет')
            self.assertEqual(p.selection, (21, 25))
        self.assertEqual(len(p.writes), 100)
        self.assertEqual(p.scrolls, [])

    def test_hidden_stream_does_not_rewrite_history(self):
        p = NativeEditorDouble()
        p.redraw_history()
        for n in range(100):
            p.stream['reasoning'] = str(n)
            p.redraw_history()
        self.assertEqual(len(p.writes), 1)
        self.assertEqual(p.active, 111)

    def test_new_response_is_visible_without_touching_draft(self):
        p = NativeEditorDouble()
        p.redraw_history()
        p.events.append({'kind': 'assistant', 'text': 'First task completed'})
        p.redraw_history()
        self.assertIn('First task completed', p.writes[-1][1])
        self.assertEqual(p.active, 111)

    def test_history_still_scrolls_when_not_composing(self):
        p = NativeEditorDouble()
        p.active = 110
        p.redraw_history()
        self.assertEqual(len(p.scrolls), 1)
        p.redraw_history()
        self.assertEqual(len(p.scrolls), 1)

    def test_recreated_native_control_is_populated_again(self):
        p = NativeEditorDouble()
        p.redraw_history()
        p.history_text = None
        p.redraw_history()
        self.assertEqual(len(p.writes), 2)

if __name__ == '__main__':
    unittest.main()
