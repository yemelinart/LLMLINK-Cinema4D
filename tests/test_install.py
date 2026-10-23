import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('c4d_installer', ROOT / 'tools' / 'install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_update_keeps_backup_outside_scanned_plugins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs' / 'plugins' / 'LLMLINK'
            first = installer.install(path)
            self.assertIsNone(first['backup'])
            (path / 'previous-version.txt').write_text('original')
            result = installer.install(path)
            backup = Path(result['backup'])
            self.assertNotEqual(backup.parent, path.parent)
            self.assertEqual((backup / 'previous-version.txt').read_text(), 'original')
            self.assertTrue((path / 'LLMLINK.pyp').is_file())
            runtime = json.loads((path / 'runtime.json').read_text())
            self.assertTrue(Path(runtime['python']).is_file())

