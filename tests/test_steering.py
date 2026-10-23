import sys,socket,tempfile,threading,time
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from companion import Session
class SteeringTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair();self.s=Session(self.a,'x'*40,self.tmp.name)
  self.release=threading.Event();self.seen=[];self.executed=[]
  self.s.config['apply_mode']='auto'
  self.s.command=lambda operation,*a,**k:self.command(operation)
 def command(self,operation):
  if operation=='python':self.executed.append('python')
  return {'ok':True,'scene':{'document':'doc','revision':0},'operation':operation}
 def tearDown(self):
  self.release.set();self.s.stop();self.wait(lambda:not self.s.busy)
  self.a.close();self.b.close();self.tmp.cleanup()
 def wait(self,p):
  deadline=time.monotonic()+3
  while not p() and time.monotonic()<deadline:time.sleep(.01)
  self.assertTrue(p())
 def model(self,config,history):
  text=next(i['content'] for i in reversed(history) if i['role']=='user');self.seen.append(text)
  if text=='original':
   while not self.release.wait(.01):self.s.provider.check()
   return {'action':'python','message':'stale','code':'pass'}
  return {'action':'done','message':'updated','code':''}
 def test_steer_cancels_generation_before_old_code(self):
  self.s.provider.step=self.model
  self.s.chat('original');self.wait(lambda:self.s.phase=='model')
  self.s.chat('later');self.s.chat('make it blue',steer=True)
  self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.seen,['original','make it blue','later']);self.assertEqual(self.executed,[])
  self.assertFalse(self.s.queue_paused)
 def test_steer_supersedes_manual_approval(self):
  self.release.set();self.s.config['apply_mode']='manual';self.s.provider.step=self.model
  self.s.chat('original');self.wait(lambda:self.s.pending is not None)
  old_id=self.s.pending['id'];self.s.chat('updated',steer=True);self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.executed,[])
  with self.assertRaises(ValueError):self.s.approve(old_id,True)
 def test_steer_waits_for_running_python_result(self):
  self.release.set();self.s.provider.step=self.model
  in_python=threading.Event();finish=threading.Event()
  def command(op,*a,**k):
   if op=='python':in_python.set();finish.wait(2);self.executed.append(op)
   return {'ok':True,'scene':{'document':'doc','revision':0},'operation':op}
  self.s.command=command
  self.s.chat('original');self.wait(in_python.is_set)
  self.s.chat('updated',steer=True)
  self.assertEqual(self.seen,['original']);self.assertFalse(self.s.provider.cancelled.is_set())
  finish.set();self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.executed,['python']);self.assertEqual(self.seen,['original','updated'])
 def test_promote_and_remove_queued_items(self):
  self.s.provider.step=self.model;self.s.chat('original');self.wait(lambda:bool(self.seen))
  self.s.chat('later');ident=self.s.queue[0]['id'];self.s.chat('remove')
  self.s.queue_remove(self.s.queue[-1]['id']);self.s.queue_now(ident);self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.seen,['original','later']);self.assertEqual(self.executed,[])

 def test_rapid_steers_preserve_every_update_before_fifo_queue(self):
  self.release.set();self.s.provider.step=self.model
  in_python=threading.Event();finish=threading.Event()
  def command(op,*a,**k):
   if op=='python':in_python.set();finish.wait(2);self.executed.append(op)
   return {'ok':True,'scene':{'document':'doc','revision':0},'operation':op}
  self.s.command=command
  self.s.chat('original');self.wait(in_python.is_set)
  self.s.chat('later')
  for text in ('steer A','steer B','steer C'):self.s.chat(text,steer=True)
  finish.set();self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.seen,['original','steer A','steer B','steer C','later'])

 def test_promotions_do_not_displace_an_already_pending_steer(self):
  self.s.busy=True;self.s.phase='python'
  self.s.chat('ordinary A');self.s.chat('ordinary B')
  first,second=[i['id'] for i in self.s.queue]
  self.s.chat('steer A',steer=True);pending=self.s.steer_pending
  self.s.queue_now(second);self.s.queue_now(first);self.s.queue_now(pending)
  self.assertEqual([i['text'] for i in self.s.queue],['steer A','ordinary B','ordinary A'])
  self.assertEqual(self.s.steer_pending,pending)
  self.s.busy=False;self.s.clear_queue()

 def test_new_steer_during_an_update_does_not_skip_older_pending_updates(self):
  self.release.set();in_python=threading.Event();finish=threading.Event();in_first_update=threading.Event()
  def command(op,*a,**k):
   if op=='python':in_python.set();finish.wait(2)
   return {'ok':True,'scene':{'document':'doc','revision':0},'operation':op}
  self.s.command=command
  def model(config,history):
   text=next(i['content'] for i in reversed(history) if i['role']=='user');self.seen.append(text)
   if text=='original':return {'action':'python','message':'original','code':'pass'}
   if text=='steer A':
    in_first_update.set()
    while True:time.sleep(.01);self.s.provider.check()
   return {'action':'done','message':'updated','code':''}
  self.s.provider.step=model
  self.s.chat('original');self.wait(in_python.is_set);self.s.chat('later')
  for text in ('steer A','steer B','steer C'):self.s.chat(text,steer=True)
  finish.set();self.wait(in_first_update.is_set)
  self.s.chat('steer D',steer=True);self.wait(lambda:not self.s.busy and not self.s.queue)
  self.assertEqual(self.seen,['original','steer A','steer B','steer C','steer D','later'])
