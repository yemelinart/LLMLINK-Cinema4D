import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from providers import Providers
class ReasoningTests(unittest.TestCase):
    def test_wire_modes(self):
        for effort,expected in [('off','none'),('low','low'),('medium','medium'),('xhigh','xhigh'),('',None)]:
            requests=[]
            def lines(request,cancel,timeout=300):
                requests.append(json.loads(request.data))
                yield b'data: {"choices":[{"delta":{"content":"{\\"action\\":\\"done\\",\\"message\\":\\"OK\\",\\"code\\":\\"\\"}"}}]}\n'
                yield b'data: [DONE]\n'
            with patch('providers.lm_lines',lines):
                p=Providers('/tmp',lambda *a,**k:None)
                result=p.lmstudio({'model':'qwen','effort':effort},'hello')
            self.assertEqual(result['action'],'done')
            self.assertEqual(requests[0].get('reasoning_effort'),expected)
