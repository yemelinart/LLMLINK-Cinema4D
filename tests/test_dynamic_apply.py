import sys,socket,tempfile,threading,time,json
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from companion import Session
class DynamicApplyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair();self.s=Session(self.a,'x'*40,self.tmp.name)
  self.commands=[];self.plan_release=threading.Event();self.plan_started=threading.Event()
  self.first_python=threading.Event();self.python_release=threading.Event();self.python_release.set()
  self.steps=0;self.s.provider.step=self.step;self.s.command=self.command
 def step(self,config,history):
  self.steps+=1
  if self.steps==1:
   self.plan_started.set()
   while not self.plan_release.wait(.01):self.s.provider.check()
  if self.steps<=2:return {'action':'python','message':'Change','code':'pass'}
  return {'action':'done','message':'Done','code':''}
 def command(self,operation,*a,**kw):
  if operation=='python':
   self.commands.append(operation)
   if len(self.commands)==1:self.first_python.set();self.python_release.wait(2)
  return {'ok':True,'scene':{'document':'doc','revision':0},'operation':operation}
 def tearDown(self):
  self.plan_release.set();self.python_release.set();self.s.stop();self.wait(lambda:not self.s.busy)
  self.a.close();self.b.close();self.tmp.cleanup()
 def wait(self,p):
  end=time.monotonic()+3
  while not p() and time.monotonic()<end:time.sleep(.005)
  self.assertTrue(p())
 def test_pending_manual_to_auto_continues_without_extra_apply(self):
  self.plan_release.set();self.s.chat('two steps');self.wait(lambda:self.s.pending is not None)
  old=self.s.pending['id'];self.assertEqual(self.commands,[])
  self.s.settings({'apply_mode':'auto'});self.wait(lambda:not self.s.busy)
  self.assertEqual(len(self.commands),2);self.assertIsNone(self.s.pending)
  with self.assertRaises(ValueError):self.s.approve(old,True)
 def test_auto_to_manual_during_generation_blocks_next_change(self):
  self.s.settings({'apply_mode':'auto'});self.s.chat('two steps');self.wait(self.plan_started.is_set)
  self.s.settings({'apply_mode':'manual'});self.plan_release.set();self.wait(lambda:self.s.pending is not None)
  self.assertEqual(self.commands,[])
  self.s.settings({'apply_mode':'auto'});self.wait(lambda:not self.s.busy)
  self.assertEqual(len(self.commands),2)
 def test_running_python_finishes_then_next_step_waits(self):
  self.plan_release.set();self.python_release.clear();self.s.settings({'apply_mode':'auto'})
  self.s.chat('two steps');self.wait(self.first_python.is_set)
  self.s.settings({'apply_mode':'manual'});self.assertEqual(len(self.commands),1)
  self.python_release.set();self.wait(lambda:self.s.pending is not None)
  self.assertEqual(len(self.commands),1)
  self.s.settings({'apply_mode':'auto'});self.wait(lambda:not self.s.busy)
  self.assertEqual(len(self.commands),2)
 def test_mode_persists_but_provider_change_remains_blocked(self):
  self.s.busy=True;self.s.settings({'apply_mode':'auto'})
  self.assertEqual(json.loads((Path(self.tmp.name)/'settings.json').read_text())['apply_mode'],'auto')
  with self.assertRaises(ValueError):self.s.settings({'provider':'lmstudio'})
  self.s.busy=False
