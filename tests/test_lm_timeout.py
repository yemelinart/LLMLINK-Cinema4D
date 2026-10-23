import copy
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from providers import prompt_history, lm_lines, Cancelled

class ContextTests(unittest.TestCase):
    def test_preserves_history_and_results_with_single_full_scene(self):
        scene={'document':'a','revision':2,'object_count':1,'objects':[{'name':'House','location':[1.23456789,0,0]}]}
        history=[{'role':'tool','content':{'ok':True,'output':'created','scene':scene}}, {'role':'user','content':'hello'}, {'role':'tool','content':{'ok':True,'scene':scene}}]
        before=copy.deepcopy(history)
        result=prompt_history(history)
        self.assertEqual(history,before)
        self.assertNotIn('objects',result[0]['content']['scene'])
        self.assertEqual(result[0]['content']['output'],'created')
        self.assertEqual(result[-1]['content']['scene']['objects'][0]['location'][0],1.2346)

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.release=threading.Event()
        release=self.release
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                if self.path=='/redirect':
                    self.send_response(302); self.send_header('Location','http://example.com'); self.end_headers();return
                self.send_response(200);self.end_headers()
                if self.path=='/slow': release.wait(3)
                try:self.wfile.write(b'data: {"ok":true}\n\ndata: [DONE]\n')
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def tearDown(self):
        self.release.set();self.server.shutdown();self.server.server_close()
    def request(self,path):
        return urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}{path}',data=b'{}')
    def test_stream(self):
        self.assertIn(b'data: [DONE]\n',list(lm_lines(self.request('/ok'),threading.Event(),2)))
    def test_cancel_during_silent_prefill(self):
        cancel=threading.Event();threading.Timer(.1,cancel.set).start();start=time.monotonic()
        with self.assertRaises(Cancelled):list(lm_lines(self.request('/slow'),cancel,2))
        self.assertLess(time.monotonic()-start,.8)
    def test_deadline_during_silent_prefill(self):
        with self.assertRaisesRegex(RuntimeError,'LM Studio'):
            list(lm_lines(self.request('/slow'),threading.Event(),.2))
    def test_redirect_not_followed(self):
        with self.assertRaisesRegex(RuntimeError,'HTTP 302'):
            list(lm_lines(self.request('/redirect'),threading.Event(),2))
