"""Read installed provider catalogs without loading a local model or editing a scene."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin' / 'LLMLINK' / 'llmlink_c4d'))
from providers import Providers

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['codex','lmstudio'],required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='llmlink-c4d-probe-') as folder:
        client=Providers(folder,lambda *a,**kw:None)
        try:
            print(json.dumps({'provider':args.provider,'models':client.models({'provider':args.provider,'endpoint':'http://127.0.0.1:1234/v1'})},ensure_ascii=False,indent=2))
        finally:client.stop()
if __name__=='__main__':main()
