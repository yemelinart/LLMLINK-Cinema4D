import sys,socket,tempfile,threading,time,json
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from companion import Session
class QueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair();self.s=Session(self.a,'x'*40,self.tmp.name)
  self.s.command=lambda *a,**k:{'ok':True,'scene':{'document':'doc','revision':0}}
  self.started=[];self.release=threading.Event()
  def step(config,history):
   self.started.append(next(i['content'] for i in reversed(history) if i['role']=='user'))
   while not self.release.wait(.01):self.s.provider.check()
   return {'action':'done','message':'Done','code':''}
  self.s.provider.step=step
 def tearDown(self):
  self.release.set();self.s.stop();self.wait(lambda:not self.s.busy)
  self.a.close();self.b.close();self.tmp.cleanup()
 def wait(self,predicate):
  deadline=time.monotonic()+3
  while not predicate() and time.monotonic()<deadline:time.sleep(.01)
  self.assertTrue(predicate())
 def test_thirty_messages_fifo(self):
  self.s.chat('first');self.wait(lambda:len(self.started)==1)
  for n in range(30):self.s.chat(str(n))
  self.assertEqual(len(self.s.queue),30)
  self.assertEqual(len([h for h in self.s.history if h['role']=='user']),1)
  self.release.set();self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.started,['first']+list(map(str,range(30))))
  self.assertEqual(len([e for e in self.s.events if e['kind']=='user']),31)
 def test_stop_pauses_then_resume(self):
  self.s.chat('first');self.wait(lambda:bool(self.started));self.s.chat('second');self.s.stop()
  self.wait(lambda:not self.s.busy)
  self.assertEqual(len(self.s.queue),1);self.assertTrue(self.s.queue_paused)
  self.release.set();self.s.resume_queue();self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.started,['first','second'])
 def test_error_pauses(self):
  self.s.busy=True;self.s.chat('later');self.s.busy=False
  self.s.provider.step=lambda *a:(_ for _ in ()).throw(RuntimeError('test failure'))
  self.s.run(self.s.config)
  self.assertTrue(self.s.queue_paused);self.assertEqual(len(self.s.queue),1)
 def test_restart_restores_paused_and_clear(self):
  self.s.busy=True;self.s.chat('later',prepared=[{'name':'ref','kind':'reference','source':'/tmp/ref'}]);self.s.busy=False
  other=Session(self.a,'x'*40,self.tmp.name)
  self.assertTrue(other.queue_paused);self.assertEqual(other.queue[0]['references'][0]['name'],'ref')
  other.clear_queue();self.assertEqual(json.loads((Path(self.tmp.name)/'queue.json').read_text()),[])
 def test_off_setting_accepted(self):
  self.s.settings({**self.s.config,'provider':'lmstudio','effort':'off'})
  self.assertEqual(self.s.config['effort'],'off')

 def test_restart_preserves_pending_steer_order_and_attachments(self):
  self.s.busy=True;self.s.phase='python'
  self.s.chat('later',prepared=[{'name':'later.png','kind':'image','source':'/tmp/later.png'}])
  for text in ('steer A','steer B'):
   self.s.chat(text,prepared=[{'name':text+'.txt','kind':'text','source':'/tmp/'+text+'.txt'}],steer=True)
  other=Session(self.a,'x'*40,self.tmp.name)
  self.assertTrue(other.queue_paused);self.assertFalse(other.busy)
  self.assertEqual([i['text'] for i in other.queue],['steer A','steer B','later'])
  self.assertEqual([bool(i.get('steer')) for i in other.queue],[True,True,False])
  self.assertEqual(other.queue[0]['references'][0]['name'],'steer A.txt')
  self.assertEqual(other.queue[-1]['references'][0]['name'],'later.png')
  self.s.busy=False;self.s.clear_queue()

 def test_interrupted_task_reopens_paused_without_replaying(self):
  (Path(self.tmp.name)/'active-task.json').write_text(json.dumps({'id':'interrupted','text':'create object'}))
  other=Session(self.a,'x'*40,self.tmp.name)
  self.assertTrue(other.queue_paused)
  self.assertFalse(other.busy)
  self.assertTrue(any('interrupted' in e.get('text','') for e in other.events))
  self.assertEqual(len(other.queue),0)
