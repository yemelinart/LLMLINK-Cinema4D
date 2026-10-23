import ast
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugin/LLMLINK/llmlink_c4d'))
from code_policy import validate_code, UnsafeCode
from context_window import excerpt, project
from chat_store import ChatStore
from companion import Session
from persistence import atomic_json
from process_io import read_process, MAX_LINE

class PolicyTests(unittest.TestCase):
 def test_system_and_native_bypasses_rejected(self):
  for code in ["import os; os.system('echo bad')",'from subprocess import run',"open('/tmp/unsafe','w')", "__import__('socket')", 'getattr(c4d, "CallCommand")(1)', 'c4d.CallCommand(12099)', 'c4d.documents.KillDocument(doc)', 'c4d.documents.SaveDocument(doc,"x",0,0)', 'c4d.documents.RenderDocument(doc,None,None,0)', 'x=(1).__class__.__bases__', 'from c4d import storage', 'import c4d.storage as s', 'from c4d import *', 'llmlink.output.mkdir()', 'exec("pass")']:
   with self.subTest(code=code), self.assertRaises(UnsafeCode):validate_code(code)
 def test_normal_modeling_animation_and_helpers_allowed(self):
  for code in ['import c4d, math\nx=c4d.BaseObject(c4d.Ocube)\ndoc.InsertObject(x)\ndoc.AddUndo(c4d.UNDOTYPE_NEWOBJ,x)', 'x=llmlink.find("Body").GetClone(c4d.COPYFLAGS_0)', 'llmlink.set_key(obj,1000,0,1)', 'llmlink.render(width=320,height=240)', 'llmlink.save_scene("Preview")', 'print(hasattr(c4d, "Ocube"))']:
   validate_code(code)
 def test_invalid_code_rejected_before_execution(self):
  with self.assertRaises(SyntaxError):validate_code('if :')

class PersistenceTests(unittest.TestCase):
 def test_failed_atomic_write_preserves_previous_file(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'state.json';atomic_json(path,{'good':1})
   with patch('persistence.os.replace',side_effect=OSError('disk failed')):
    with self.assertRaises(OSError):atomic_json(path,{'bad':2})
   self.assertEqual(json.loads(path.read_text()),{'good':1})
   self.assertEqual(list(Path(tmp).iterdir()),[path])
   self.assertEqual(path.stat().st_mode&0o777,0o600)
 def test_draft_save_never_rewrites_full_archive(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=ChatStore(tmp);ident='a'*32
   store.save(ident,[{'role':'user','content':'first'}],[],None,{})
   before=(store.root/(ident+'.json')).stat().st_mtime_ns
   for n in range(20):store.save_draft(ident,{'text':'draft '+str(n)})
   self.assertEqual(before,(store.root/(ident+'.json')).stat().st_mtime_ns)
   self.assertEqual(store.load(ident)['draft']['text'],'draft 19')
 def test_small_excerpt_really_stays_bounded(self):
  for n in (0,1,10,70,71,72,100):self.assertLessEqual(len(excerpt('x'*1000,n)),n)
 def test_projection_does_not_deepcopy_old_scenes(self):
  class Expensive(dict):
   def __deepcopy__(self,memo):raise AssertionError('Historical scene copied')
  old=Expensive(document='doc',revision='old',objects=[{'name':'old'}])
  history=[{'role':'tool','content':{'scene':old}}, {'role':'tool','content':{'scene':{'document':'doc','revision':'new','objects':[]}}}]
  result=project(history)
  self.assertEqual(result[0]['content']['scene']['revision'],'old')
  self.assertIs(history[0]['content']['scene'],old)

class QueueDurabilityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair()
  self.s=Session(self.a,'x'*40,self.tmp.name);self.s.send=lambda v:None
 def tearDown(self):
  self.s.stop();self.a.close();self.b.close();self.tmp.cleanup()
 def test_crash_between_active_record_and_queue_removal_cannot_replay(self):
  self.s.busy=True;self.s.chat('committed');self.s.chat('next');self.s.busy=False
  item=self.s.queue[0]
  atomic_json(Path(self.tmp.name)/'active-task.json',{'id':item['id'],'item':item})
  restored=Session(self.a,'x'*40,self.tmp.name)
  self.assertEqual([i['text'] for i in restored.queue],['next'])
  self.assertTrue(restored.queue_paused);self.assertFalse(restored.busy)
 def test_failure_to_record_active_task_never_sticks_busy_or_drops_queue(self):
  self.s.busy=True;self.s.chat('keep me');self.s.busy=False
  with patch('companion.atomic_json',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.s.start_next()
  self.assertFalse(self.s.busy);self.assertTrue(self.s.queue_paused)
  self.assertEqual(self.s.queue[0]['text'],'keep me')
 def test_failed_admission_is_not_secretly_queued(self):
  self.s.busy=True
  with patch.object(self.s,'save_queue',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.s.chat('rejected')
  self.assertFalse(self.s.queue);self.s.busy=False
 def test_queued_task_never_moves_to_another_document(self):
  self.s.command=lambda *a,**k:{'ok':True,'scene':{'document':'different','revision':'r'}}
  self.s.provider.step=lambda *a:self.fail('Model must not plan for wrong document')
  self.s.run(self.s.config,'original')
  self.assertTrue(self.s.queue_paused)
  self.assertTrue(any('another document' in e.get('text','') for e in self.s.events))

class PipeTests(unittest.TestCase):
 def proc(self,stdout):
  return SimpleNamespace(stdout=io.StringIO(stdout),stderr=io.StringIO('diagnostic\n'))
 def test_oversized_line_is_reported_not_parsed(self):
  proc=self.proc('x'*(MAX_LINE+1));events,errors=read_process(proc,threading.Event())
  self.assertIsInstance(events.get(timeout=2),RuntimeError)
  proc.llmlink_reader_done.set()
 def test_backpressure_bounds_queue_and_shutdown_unblocks_reader(self):
  proc=self.proc('{}\n'*10000);events,errors=read_process(proc,threading.Event())
  time.sleep(.05)
  self.assertLessEqual(events.qsize(),32)
  proc.llmlink_reader_done.set()
 def test_wrong_event_type_is_rejected(self):
  proc=self.proc('[]\n');events,_=read_process(proc,threading.Event())
  self.assertIsInstance(events.get(timeout=2),RuntimeError)
  proc.llmlink_reader_done.set()

# Test production UI/bridge methods without importing the Cinema-only module.
def method(file,cls,name,namespace):
 tree=ast.parse((ROOT/'plugin/LLMLINK/llmlink_c4d'/file).read_text())
 node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
 node=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),namespace)
 return namespace[name]

class NativeBoundaryTests(unittest.TestCase):
 def test_failed_bridge_does_not_early_return_on_live_process(self):
  ns={'RuntimeError':RuntimeError}
  start=method('bridge.py','Bridge','start',ns)
  class ReachedRestart(Exception):pass
  b=SimpleNamespace(process=SimpleNamespace(poll=lambda:None),failed=True,executor=SimpleNamespace(render=None),close=lambda:(_ for _ in ()).throw(ReachedRestart()))
  with self.assertRaises(ReachedRestart):start(b)
 def test_rejected_message_does_not_overwrite_new_draft(self):
  ns=dict(COMPOSER=111,STATUS=114,CODE=132,APPLY=133,DECLINE=134,RESUME=141,REMOVE=142,PROMOTE=143,CLEAR_QUEUE=144,CONNECT=103,SETTINGS=160,Path=Path)
  receive=method('dialog.py','LLMLINKDialog','receive',ns)
  writes=[]
  d=SimpleNamespace(events=[],pending_submissions={'first':{'text':'earlier rejected','attachments':[]}},attachments=[],GetString=lambda _: 'new draft',SetString=lambda i,t:writes.append((i,t)),set_files=lambda:None)
  receive(d,{'type':'event','event':{'kind':'error','text':'failed','client_id':'first'}})
  self.assertFalse(any(i==111 for i,t in writes))
  self.assertIn('earlier rejected',d.events[-1]['text'])
 def test_transport_failure_reenables_connect_and_clears_busy(self):
  ns=dict(STATUS=114,CODE=132,APPLY=133,DECLINE=134,RESUME=141,REMOVE=142,PROMOTE=143,CLEAR_QUEUE=144,CONNECT=103,SETTINGS=160)
  receive=method('dialog.py','LLMLINKDialog','receive',ns)
  enabled={};d=SimpleNamespace(state={'busy':True},Enable=lambda i,v:enabled.update({i:v}),SetString=lambda *a:None)
  receive(d,{'type':'bridge_error','error':'disconnected'})
  self.assertFalse(d.state['busy']);self.assertTrue(enabled[103]);self.assertFalse(enabled[133])


class AdmissionAndQueueWriteTests(unittest.TestCase):
 setUp=QueueDurabilityTests.setUp
 tearDown=QueueDurabilityTests.tearDown
 def test_invalid_config_rejects_submission_instead_of_using_old_provider(self):
  self.s.busy=True
  with self.assertRaises(ValueError):self.s.chat('do not silently send',config={**self.s.config,'provider':'lmstudio'})
  self.assertFalse(self.s.queue);self.s.busy=False
 def test_queue_removal_failure_preserves_order(self):
  self.s.busy=True
  self.s.chat('first');self.s.chat('second');self.s.busy=False
  ids=[i['id'] for i in self.s.queue]
  with patch.object(self.s,'save_queue',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.s.queue_remove(ids[0])
   with self.assertRaises(OSError):self.s.queue_now(ids[1])
  self.assertEqual([i['id'] for i in self.s.queue],ids)
  self.assertFalse(self.s.queue[1].get('steer'))
 def test_settings_disk_failure_keeps_previous_config(self):
  old=dict(self.s.config)
  with patch('companion.atomic_json',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.s.settings({'apply_mode':'auto'})
  self.assertEqual(self.s.config,old)

class UIRefreshTests(unittest.TestCase):
 def test_same_chat_refresh_preserves_unsaved_draft(self):
  receive=method('dialog.py','LLMLINKDialog','receive',{'COMPOSER':111})
  writes=[];d=SimpleNamespace(state={'chat_id':'same'},SetString=lambda *a:writes.append(a),set_files=lambda:None,attachments=['current.png'])
  receive(d,{'type':'chat_opened','id':'same','draft':{'text':'stale','attachments':[]}})
  self.assertEqual(writes,[]);self.assertEqual(d.attachments,['current.png'])
 def test_docking_recreation_restores_saved_draft(self):
  receive=method('dialog.py','LLMLINKDialog','receive',{'COMPOSER':111})
  writes=[];d=SimpleNamespace(state={'chat_id':'same'},restore_draft=True,SetString=lambda *a:writes.append(a),set_files=lambda:None,attachments=[])
  receive(d,{'type':'chat_opened','id':'same','draft':{'text':'saved','attachments':['image.png']}})
  self.assertEqual(writes,[(111,'saved')]);self.assertEqual(d.attachments,['image.png'])

class AttachmentIntegrityTests(unittest.TestCase):
 def test_codex_rejects_modified_image_before_spawning(self):
  from providers import Providers
  import hashlib
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'image.png';path.write_bytes(b'changed')
   client=Providers(folder,lambda *a,**k:None)
   client.images=[{'path':str(path),'sha256':hashlib.sha256(b'original').hexdigest()}]
   with patch.object(client,'spawn',side_effect=AssertionError('Must not send modified file')):
    with self.assertRaisesRegex(ValueError,'changed'):client.codex({'provider':'codex'},'hello')

class CodexProtocolTests(unittest.TestCase):
 def test_early_completion_and_redacted_mcp_config(self):
  from providers import Providers
  answer={'action':'done','message':'Verified','code':''}
  frames=[{'id':1,'result':{}},
          {'id':2,'result':{'config':{'mcp_servers':{'example':{'command':'','tool_timeout_sec':''}}}}},
          {'id':3,'result':{'thread':{'id':'test-thread'}}},
          {'method':'item/completed','params':{'item':{'type':'agentMessage','text':json.dumps(answer)}}},
          {'method':'turn/completed','params':{'turn':{'status':'completed'}}},
          {'id':4,'result':{}}]
  proc=SimpleNamespace(stdout=io.StringIO(''.join(json.dumps(f)+'\n' for f in frames)),stderr=io.StringIO(),stdin=io.StringIO())
  with tempfile.TemporaryDirectory() as folder:
   p=Providers(folder,lambda *a,**k:None)
   with patch.object(p,'spawn',return_value=proc),patch.object(p,'close_process'):
    self.assertEqual(p.codex({'provider':'codex','model':'test'},'check'),answer)
   sent=[json.loads(s) for s in proc.stdin.getvalue().splitlines()]
   config=next(s['params']['config'] for s in sent if s.get('method')=='thread/start')
   self.assertIs(config['mcp_servers.example.enabled'],False)
   self.assertNotIn('mcp_servers',config)
   self.assertIs(config['features.apps'],False)
   proc.llmlink_reader_done.set()

if __name__=='__main__':unittest.main()
