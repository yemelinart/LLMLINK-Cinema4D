"""Recovery and message admission must work with real queued/session state."""
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from companion import Session


class RecoveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair()
        self.s=Session(self.a,'x'*40,self.temp.name)
        self.s.send=lambda value:None
        self.s.config['apply_mode']='auto'
        self.started=threading.Event();self.release=threading.Event();self.seen=[];self.codes=[]
        self.s.command=self.command

    def tearDown(self):
        self.release.set();self.s.stop();self.wait(lambda:not self.s.busy)
        self.a.close();self.b.close();self.temp.cleanup()

    def wait(self,p):
        end=time.monotonic()+4
        while not p() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(p())

    def command(self,operation,code='',*args,**kwargs):
        result={'ok':True,'operation':operation,'scene':{'document':'test','revision':0}}
        if operation=='python':
            self.codes.append(code)
            if code=='bad':result.update(ok=False,error="AttributeError: 'ActionLayer' object has no attribute 'channels'",failure_kind='python',api_hint='Use llmlink.fcurves(obj)')
        return result

    def model(self,config,history):
        text=next(item['content'] for item in reversed(history) if item['role']=='user')
        if not self.started.is_set():
            self.started.set()
            while not self.release.wait(.005):self.s.provider.check()
        self.seen.append(text)
        return {'action':'python','message':'Try code','code':'bad'} if text=='broken' else {'action':'done','message':'Next task complete','code':''}

    def test_exhausted_python_repair_advances_thirty_queued_jobs(self):
        self.s.provider.step=self.model
        self.s.chat('broken');self.wait(self.started.is_set)
        for i in range(30):self.s.chat('task '+str(i),client_id='client-'+str(i))
        self.release.set();self.wait(lambda:not self.s.busy and not self.s.queue)
        self.assertEqual(self.codes,['bad']*4)
        self.assertEqual(self.seen,['broken']*4+['task '+str(i) for i in range(30)])
        self.assertFalse(self.s.queue_paused)
        self.assertEqual(len([e for e in self.s.events if e['kind']=='error']),1)
        acknowledgements=[e for e in self.s.events if e['kind']=='user']
        self.assertEqual(acknowledgements[-1]['client_id'],'client-29')

    def test_error_count_resets_after_a_successful_python_step(self):
        replies=iter(['bad','good','bad','good','done'])
        def model(*args):
            code=next(replies)
            return {'action':'done' if code=='done' else 'python','message':code,'code':'' if code=='done' else code}
        self.s.provider.step=model
        self.s.chat('several independent corrections');self.wait(lambda:not self.s.busy)
        self.assertEqual(self.codes,['bad','good','bad','good'])
        self.assertFalse([e for e in self.s.events if e['kind']=='error'])
        self.assertEqual(self.s.activity['state'],'done')

    def test_failed_step_cannot_be_reported_as_successful_completion(self):
        replies=iter([{'action':'python','message':'Code','code':'bad'},
                      {'action':'done','message':'All finished','code':''}])
        self.s.provider.step=lambda *args:next(replies)
        self.s.chat('broken');self.wait(lambda:not self.s.busy)
        self.assertEqual(self.s.activity['state'],'error')
        self.assertTrue(any('not repaired' in e.get('text','') for e in self.s.events if e['kind']=='error'))

    def test_uncertain_scene_accepts_queue_without_executing_or_resuming(self):
        self.s.uncertain='inflight';self.s.provider.step=self.model
        for i in range(30):self.s.chat('later '+str(i),client_id='uncertain-'+str(i))
        self.s.chat('refine after acknowledgement',steer=True,client_id='update')
        self.assertEqual(len(self.s.queue),31)
        self.assertTrue(self.s.queue_paused);self.assertFalse(self.started.is_set())
        self.assertIsNone(self.s.steer_pending)
        self.assertEqual(self.s.queue[0]['text'],'refine after acknowledgement')
        self.s.uncertain=None
        self.assertFalse(self.started.is_set())
        self.release.set();self.s.resume_queue();self.wait(lambda:not self.s.busy and not self.s.queue)
        self.assertEqual(len(self.seen),31)

    def test_connection_failure_keeps_the_remaining_queue_paused(self):
        def model(config,history):
            self.started.set()
            while not self.release.wait(.005):self.s.provider.check()
            raise ConnectionError('Model server unavailable')
        self.s.provider.step=model
        self.s.chat('first');self.wait(self.started.is_set);self.s.chat('next')
        self.release.set();self.wait(lambda:not self.s.busy)
        self.assertEqual(len(self.s.queue),1);self.assertTrue(self.s.queue_paused)
        for i in range(30):self.s.chat('more '+str(i))
        self.assertEqual(len(self.s.queue),31);self.assertFalse(self.s.busy)

    def test_checkpoint_precondition_failure_does_not_repair_or_drain_queue(self):
        def command(operation,code='',*args,**kwargs):
            if operation=='python':return {'ok':False,'failure_kind':'precondition','error':'Not enough free disk space for a checkpoint'}
            return {'ok':True,'scene':{'document':'test','revision':0}}
        self.s.command=command;self.s.provider.step=self.model
        self.s.chat('broken');self.wait(self.started.is_set);self.s.chat('next');self.release.set()
        self.wait(lambda:not self.s.busy)
        self.assertEqual(len(self.s.queue),1);self.assertTrue(self.s.queue_paused)
        self.assertEqual(self.seen,['broken'])

    def test_syntax_feedback_is_repaired_without_executing_invalid_python(self):
        calls=[]
        def model(config,history):
            calls.append(1)
            if len(calls)==1:
                raise SyntaxError('invalid syntax',('<LLMLINK preview>',3,1,'if :'))
            if len(calls)==2:
                error=history[-1]['content']
                self.assertFalse(error['executed']);self.assertEqual(error['error_line'],3)
                return {'action':'python','message':'Repaired','code':'good'}
            return {'action':'done','message':'Complete','code':''}
        self.s.provider.step=model;self.s.chat('syntax recovery');self.wait(lambda:not self.s.busy)
        self.assertEqual(self.codes,['good'])
        self.assertEqual(self.s.activity['state'],'done')
