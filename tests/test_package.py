import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_extracted_bundle_installs_without_source_checkout(self):
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'package.py')], check=True, capture_output=True)
        archive = ROOT / 'dist' / 'LLMLINK-Cinema4D-0.2.0-macOS.zip'
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            with zipfile.ZipFile(archive) as bundle:
                self.assertFalse(any('runtime.json' in name or '__pycache__' in name for name in bundle.namelist()))
                bundle.extractall(folder / 'extracted')
            source = folder / 'extracted' / 'LLMLINK-Cinema4D-0.2.0'
            plugins = folder / 'cinema' / 'plugins'
            result = subprocess.run([sys.executable, str(source / 'tools' / 'install.py'), '--plugins', str(plugins)],
                                    check=True, text=True, capture_output=True)
            installed = Path(json.loads(result.stdout)['installed'])
            self.assertTrue((installed / 'LLMLINK.pyp').is_file())
            self.assertTrue((installed / 'llmlink_c4d' / 'scene_tools.py').is_file())
            self.assertEqual((installed / 'llmlink_c4d' / 'providers.py').read_bytes(),
                             (ROOT / 'plugin' / 'LLMLINK' / 'llmlink_c4d' / 'providers.py').read_bytes())
