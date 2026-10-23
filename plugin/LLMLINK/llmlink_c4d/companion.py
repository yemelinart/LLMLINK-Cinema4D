"""LLMLINK local chat service. No c4d imports and no cloud intermediary."""
import argparse
import collections
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from protocol import VERSION, MAX_MESSAGE, encode, decode
from providers import Providers, Cancelled, local_base
from attachments import collect
from chat_store import ChatStore, title
from context_window import recall
from persistence import atomic_json


class SceneStepFailed(RuntimeError):
    """A known Python result could not be repaired; the next queued job may run."""


class Session:
    def __init__(self, bridge, token, data):
        self.bridge, self.token, self.data = bridge, token, Path(data)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data.chmod(0o700)
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.events = collections.deque(maxlen=1500)
        self.saved_events=collections.deque(maxlen=1500)
        self.thoughts={}
        self.sequence = 0
        self.pending = None
        self.results = {}
        self.inflight = set()
        self.uncertain = None
        self.condition = threading.Condition(self.lock)
        self.busy = False
        self.queue = collections.deque()
        self.queue_paused = False
        self.steer_pending=None
        self.phase='idle'
        self.activity={'state':'ready','since':time.time(),'task_started':0,'updated':0,'finished':0,'reason':''}
        self.connected = True
        self.scene = {}
        self.history = []
        self.catalog_lock=threading.Lock()
        self.catalog_active=set()
        self.catalog_providers=set()
        self.native_ready = False
        self.goal = None
        goal_path = self.data / "goal.json"
        if goal_path.exists():
            try:
                self.goal = json.loads(goal_path.read_text())
                if self.goal and self.goal.get("status") not in ("complete", "cancelled"):
                    self.goal["status"] = "paused"
            except (ValueError, OSError):
                pass
        self.chat_id = uuid.uuid4().hex
        self.config = {'provider': 'codex', 'model': '', 'endpoint': 'http://127.0.0.1:1234/v1',
                       'codex_path': '', 'claude_path': '', 'effort':'', 'chat_rows':'18', 'apply_mode':'manual'}
        config_path = self.data / 'settings.json'
        if config_path.exists():
            try:
                saved = json.loads(config_path.read_text())
                self.config.update({k: v for k, v in saved.items() if k in self.config and isinstance(v, str)})
            except (ValueError, OSError):
                pass
        self.runtime = self.data / 'runtime'
        self.runtime.mkdir(exist_ok=True, mode=0o700)
        self.provider = Providers(self.runtime, self.emit)
        self.store=ChatStore(self.data)
        self.draft={}
        previous=self.store.latest()
        recovery_error = None
        if previous:
            try:self.restore_chat(previous)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                recovery_error = 'The previous chat could not be opened. Its files have been preserved: '+str(exc)
        active_id = None
        try:
            active_id=json.loads((self.data/'active-task.json').read_text(encoding='utf-8')).get('id')
        except (OSError,ValueError,AttributeError):
            pass
        try:
            saved_queue=json.loads((self.data/'queue.json').read_text())
            if isinstance(saved_queue,list):
                for item in saved_queue[:100]:
                    if not isinstance(item,dict) or not all(k in item for k in ('id','text','references','config')):continue
                    # A crash between recording the active task and replacing
                    # the queue must never replay the committed task.
                    if item['id']==active_id:continue
                    if not isinstance(item['id'],str) or not isinstance(item['text'],str) or not isinstance(item['references'],list) or not isinstance(item['config'],dict):continue
                    self.queue.append(item)
                    if not any(e.get('queue_id')==item['id'] and e['kind']=='user' for e in self.saved_events):
                        self.emit('user',text=item['text'],queue_id=item['id'],queued=True,steering=bool(item.get('steer')),attachments=item['references'])
                self.queue_paused=bool(self.queue)
        except (OSError,ValueError):pass
        self.emit('status', text='Queue restored · resume when ready' if self.queue else 'Ready')
        if recovery_error:self.emit('error',text=recovery_error)
        if (self.data/'active-task.json').exists():
            self.emit('info',text='The previous task was interrupted. Inspect the scene and checkpoints before resending it. Pending tasks remain paused.')
            self.queue_paused=True

    def emit(self, kind, **value):
        with self.lock:
            self.sequence += 1
            event = {'id': self.sequence, 'kind': kind, 'time': time.time(), **value}
            self.events.append(event)
            records=[]
            if kind=='step_start':self.thoughts={}
            if kind=='stream':
                channel=value.get('channel','working')
                if self.phase=='model' and self.activity['state']!='stopping':
                    self.set_activity('thinking' if channel=='reasoning' else 'responding', updated=event['time'])
                thought=self.thoughts.setdefault(channel,{'id':event['id'],'kind':'thought','time':event['time'],'channel':channel,'text':''})
                thought['text']=(thought['text']+value.get('text',''))[-16000:]
            elif kind=='step_end':
                records.extend(self.thoughts.values());self.thoughts={}
            if kind!='stream' and not value.get('transient'):records.append(event)
            if records:
                self.saved_events.extend(records)
                with (self.data / ('chat-' + self.chat_id + '.jsonl')).open('a', encoding='utf-8') as out:
                    os.fchmod(out.fileno(),0o600)
                    for record in records:out.write(json.dumps(record,ensure_ascii=False)+'\n')
            if kind not in ('stream','step_start') and hasattr(self,'store'):
                self.save_chat()
            if self.native_ready:
                try:
                    self.send({'type':'event','event':event})
                except OSError:
                    self.connected = False
            return event

    def native_state(self):
        if self.native_ready:
            self.send({'type':'state','busy':self.busy or bool(self.uncertain),'activity':dict(self.activity),'config':self.config,'goal':self.goal,'queue_count':len(self.queue),'queue_paused':self.queue_paused,'queue_ids':[item['id'] for item in self.queue],'steer_pending':self.steer_pending or '', 'chat_id':self.chat_id,'chat_title':self.chat_title()})

    def set_activity(self, state, reason='', updated=None):
        """Volatile execution state: not reconstructed from old chat events."""
        with self.lock:
            changed=self.activity['state']!=state or self.activity['reason']!=reason
            if changed:
                self.activity.update(state=state,reason=reason,since=time.time())
            if updated is not None:self.activity['updated']=updated
            if state in ('done','error','stopped') and not self.activity['finished']:
                self.activity['finished']=time.time()
            if changed:self.native_state()

    def save_goal(self,notify=True):
        atomic_json(self.data/'goal.json',self.goal)
        if notify:self.native_state()

    def create_goal(self,text,paths=None,budget=24,client_id=""):
        if self.busy or self.uncertain or self.queue:raise ValueError('Finish the current task and queued messages before starting a goal')
        if self.goal and self.goal['status'] not in ('complete','cancelled'):
            raise ValueError('Finish or cancel the current goal first')
        if not isinstance(text,str) or not text.strip() or len(text)>16000:
            raise ValueError('Write a goal first')
        if type(budget) is not int or not 1<=budget<=100:raise ValueError('Goal budget must be 1–100 steps')
        # Validate/copy selected references before activating the goal.
        references=collect(paths or [],self.data/'attachments')
        self.goal={'id':uuid.uuid4().hex,'text':text,'status':'active','steps':0,'budget':budget,'document':self.scene.get('document'),'references':references}
        self.save_goal()
        self.chat('GOAL: '+text,prepared=references,client_id=client_id)

    def continue_goal(self,extra=0):
        if self.busy or self.uncertain or self.queue:raise ValueError('Finish queued messages before continuing a goal')
        if not self.goal or self.goal['status'] not in ('paused','review'):raise ValueError('No paused goal')
        if type(extra) is not int or not 0<=extra<=100:raise ValueError('Invalid additional budget')
        self.goal['budget']+=extra
        if self.goal['steps']>=self.goal['budget']:raise ValueError('Add more steps before continuing')
        self.goal['status']='active'
        self.goal['document']=self.scene.get('document')
        self.save_goal()
        self.chat('Continue this goal from the measured current scene: '+self.goal['text'],prepared=self.goal.get('references',[]))

    def finish_goal(self,cancel=False):
        if not self.goal:raise ValueError('No goal')
        if cancel:
            self.stop()
            self.goal['status']='cancelled'
        elif not self.busy and self.goal['status']=='review':
            self.goal['status']='complete'
        else:raise ValueError('Goal is not ready for completion review')
        self.save_goal()
        self.emit('info',text='Goal '+self.goal['status'])

    def send(self, value):
        with self.send_lock:
            self.bridge.sendall(encode(value))

    def bridge_reader(self):
        buffer = bytearray()
        try:
            while True:
                chunk = self.bridge.recv(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                for msg in decode(buffer):
                    try:
                        kind = msg.get('type')
                        if kind == 'result':
                            with self.condition:
                                ident=msg['id']
                                if ident == self.uncertain:
                                    self.uncertain = None
                                    try:(self.data/'active-task.json').unlink()
                                    except FileNotFoundError:pass
                                    self.set_activity('stopped','Completion received · review the scene before continuing')
                                    self.history.append({'role':'tool','content':msg})
                                    self.emit('tool',result=msg)
                                    self.emit('status',text='Completion received. Review the scene before continuing.')
                                    self.native_state()
                                elif ident in self.inflight:
                                    self.results[ident] = msg
                                self.condition.notify_all()
                        elif kind == 'scene':
                            with self.lock:
                                self.scene = msg['scene']
                        elif kind == 'chat':
                            self.chat(msg['text'],msg.get('attachments',[]),steer=msg.get('steer',False),client_id=msg.get('client_id',''),config=msg.get('config'))
                        elif kind == 'goal':
                            self.create_goal(msg['text'],msg.get('attachments',[]),msg.get('budget',24),client_id=msg.get('client_id',''))
                        elif kind == 'continue_goal':
                            self.continue_goal(msg.get('extra',0))
                        elif kind == 'finish_goal':
                            self.finish_goal(msg.get('cancel',False))
                        elif kind == 'draft':
                            with self.lock:
                                self.capture_draft(msg.get('draft'))
                                self.store.save_draft(self.chat_id,self.draft)
                        elif kind == 'models':
                            threading.Thread(target=self.discover_models,args=(msg['config'],),daemon=True).start()
                        elif kind == 'settings':
                            self.settings(msg['config']);self.native_state()
                        elif kind == 'new_chat':
                            self.new_chat(msg.get('draft'))
                        elif kind == 'list_chats':
                            self.send_chats()
                        elif kind == 'refresh':
                            with self.lock:
                                self.publish_chat()
                                if self.pending:
                                    self.send({'type':'approval','id':self.pending['id'],
                                               'message':self.pending['message'],'code':self.pending['code']})
                        elif kind == 'switch_chat':
                            self.switch_chat(msg['id'],msg.get('draft'))
                        elif kind == 'apply_mode':
                            self.settings({'apply_mode':msg['mode']});self.native_state()
                        elif kind == 'approve':
                            self.approve(msg['id'], msg.get('allow', False))
                        elif kind == 'queue_now':
                            self.queue_now(msg['id'])
                        elif kind == 'queue_remove':
                            self.queue_remove(msg['id'])
                        elif kind == 'queue_resume':
                            self.resume_queue()
                        elif kind == 'queue_clear':
                            self.clear_queue()
                        elif kind == 'stop':
                            self.stop()
                    except (ValueError,KeyError,OSError,TypeError) as exc:
                        self.emit('error',text=str(exc),client_id=msg.get('client_id','') if kind in ('chat','goal') else '');self.native_state()
        except (OSError, ValueError) as exc:
            self.emit('error', text=str(exc))
        finally:
            self.connected = False
            self.stop()
            self.emit('error', text='Cinema 4D disconnected. Reopen LLMLINK from Cinema 4D.',transient=True)

    def command(self, operation, code='', expected=None, checkpoint=None, prepare_edit=False):
        self.set_activity('executing' if operation=='python' else 'inspecting')
        ident = uuid.uuid4().hex
        value = {'type': 'command', 'id': ident, 'operation': operation, 'code': code,
                 'expected': expected, 'checkpoint': checkpoint, 'expires_at': time.time()+85, 'prepare_edit':prepare_edit}
        if operation=='python':
            latest=next((item for item in reversed(self.history) if item.get('role')=='user'),{})
            value['assets']=[ref['source'] for ref in latest.get('attachments',[]) if isinstance(ref.get('source'),str)]
        deadline = time.monotonic() + (1900 if operation == 'python' else 90)
        with self.condition:
            self.inflight.add(ident)
            sent=False
            try:
                self.provider.check()
                self.send(value)
                sent=True
                while ident not in self.results:
                    self.provider.check()
                    if not self.connected:
                        raise RuntimeError('Cinema 4D disconnected')
                    if time.monotonic() > deadline:
                        raise RuntimeError('Cinema 4D has not acknowledged completion. Outcome is unknown; do not retry blindly.')
                    self.condition.wait(.2)
                return self.results.pop(ident)
            except (Exception, Cancelled):
                if operation == 'python' and sent and ident not in self.results:
                    self.uncertain=ident
                    self.set_activity('uncertain', 'Waiting for Cinema 4D to acknowledge the last operation')
                raise
            finally:
                self.inflight.discard(ident)

    def approve(self, ident, allow):
        if type(allow) is not bool:
            raise ValueError('Approval decision must be true or false')
        with self.condition:
            if not self.pending or self.pending['id'] != ident or self.pending['decision'] is not None:
                raise ValueError('Approval has expired')
            self.pending['decision'] = allow
            self.condition.notify_all()

    def stop(self):
        if self.native_ready:
            try:self.send({'type':'cancel'})
            except OSError:pass
        with self.lock:self.queue_paused=True;self.steer_pending=None
        if self.busy:self.set_activity('stopping', 'Waiting for the committed Cinema 4D step' if self.phase=='python' else 'Stopping the current request')
        if self.goal and self.goal['status']=='active':
            self.goal['status']='paused'
            self.save_goal()
        self.provider.stop()
        with self.condition:
            self.condition.notify_all()

    def chat(self, text, paths=None, prepared=None, steer=False, client_id="", config=None):
        if type(steer) is not bool:raise ValueError('Delivery mode must be true or false')
        if not isinstance(client_id,str) or len(client_id)>80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in client_id):
            raise ValueError('Invalid message identifier')
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('Message must contain 1–16000 characters')
        references=prepared if prepared is not None else collect(paths or [],self.data/'attachments')
        with self.lock:
            if config is not None:self.settings(config)
            if not self.connected:
                raise ValueError('Cinema 4D is disconnected')
            if self.uncertain:self.queue_paused=True
            if len(self.queue)>=100:
                raise ValueError('Queue is full (100 messages). Wait for a task to finish.')
            if not self.busy and not self.uncertain and not self.queue:self.queue_paused=False
            queued=self.busy or bool(self.uncertain) or bool(self.queue) or self.queue_paused
            item={'id':uuid.uuid4().hex,'text':text,'references':references,'config':self.config.copy(),'steer':steer,'client_id':client_id,
                  'document':self.scene.get('document') if queued else None}
            self.enqueue(item,steer)
            try:self.save_queue()
            except Exception:
                self.queue.remove(item)
                raise
            self.draft={}
            self.emit('user', text=text, queue_id=item['id'], client_id=client_id, queued=bool(queued), steering=bool(steer), attachments=[{k:v for k,v in ref.items() if k in ('name','kind','size','path','source')} for ref in references])
            if steer and not self.uncertain:self.interrupt_for_steer(item['id'])
            self.start_next()
            self.native_state()

    def enqueue(self,item,steer=False):
        # Pending updates retain arrival order ahead of ordinary FIFO jobs.
        # A second update must never overwrite the identity of the first one.
        if steer:
            item['steer']=True
            index=next((i for i,queued in enumerate(self.queue) if not queued.get('steer')),len(self.queue))
            self.queue.insert(index,item)
        else:self.queue.append(item)

    def interrupt_for_steer(self,ident):
        self.queue_paused=False
        if self.busy:
            if self.steer_pending:return
            self.steer_pending=next((item['id'] for item in self.queue if item.get('steer')),ident)
            if self.phase=='model':self.provider.stop()
            if self.pending:self.pending['superseded']=True
            self.condition.notify_all()
            self.emit('info',text='Update received; replanning after the current Cinema 4D step.' if self.phase=='python' else 'Update received; replanning the current task.')

    def queue_now(self,ident):
        with self.condition:
            if self.uncertain:raise ValueError('Wait for Cinema 4D to acknowledge the last operation first')
            item=next((i for i in self.queue if i['id']==ident),None)
            if item is None:raise ValueError('Message is no longer queued')
            if ident==self.steer_pending:return
            previous=collections.deque(self.queue)
            previous_steer=item.get('steer',False)
            self.queue.remove(item);self.enqueue(item,True)
            try:self.save_queue()
            except Exception:
                item['steer']=previous_steer;self.queue=previous
                raise
            self.emit('queue_promoted',queue_id=ident)
            self.interrupt_for_steer(ident);self.start_next();self.native_state()

    def queue_remove(self,ident):
        with self.lock:
            item=next((i for i in self.queue if i['id']==ident),None)
            if item is None:raise ValueError('Message is no longer queued')
            if self.steer_pending==ident:raise ValueError('This update is already being applied')
            previous=collections.deque(self.queue)
            self.queue.remove(item)
            try:self.save_queue()
            except Exception:
                self.queue=previous
                raise
            self.emit('queue_removed',queue_id=ident);self.native_state()

    def save_queue(self):
        atomic_json(self.data/'queue.json',list(self.queue))

    def start_next(self):
        # Caller holds the session lock; never expose an idle gap between jobs.
        if self.busy or self.uncertain or self.queue_paused or not self.connected or not self.queue:
            return
        item=self.queue[0]
        try:
            # Publish the complete task before removing it from the durable
            # queue. Recovery filters its ID, avoiding both loss and replay.
            atomic_json(self.data/'active-task.json',{'id':item['id'],'text':item['text'],'started':time.time(),
                                                     'chat_id':self.chat_id,'item':item})
            atomic_json(self.data/'queue.json',list(self.queue)[1:])
        except Exception:
            self.queue_paused=True
            raise
        self.busy=True
        self.queue.popleft()
        self.phase='idle'
        if not item.get('document'):self.scene={}
        self.activity={'state':'inspecting','since':time.time(),'task_started':time.time(),'updated':0,'finished':0,'reason':''}
        if self.steer_pending==item['id']:self.steer_pending=None
        self.provider.cancelled.clear()
        self.history.append({'role':'user','content':item['text'],'attachments':item['references']})
        try:
            self.emit('queue_started',queue_id=item['id'])
            self.native_state()
            threading.Thread(target=self.run,args=(item['config'],item.get('document')),daemon=True).start()
        except Exception:
            self.busy=False
            self.queue_paused=True
            raise

    def resume_queue(self):
        with self.lock:
            if self.uncertain:raise ValueError('Wait for Cinema 4D to acknowledge the last operation first')
            self.queue_paused=False
            self.start_next()
            self.native_state()

    def clear_queue(self):
        with self.lock:
            atomic_json(self.data/'queue.json',[])
            for item in self.queue:self.emit('queue_removed',queue_id=item['id'])
            self.queue.clear()
            self.steer_pending=None
            self.save_queue()
            self.queue_paused=False
            self.native_state()

    def wait_for_apply(self,step):
        # The mode check and committing this step share one lock with settings.
        # A mode change affects all steps not yet committed to Cinema 4D.
        with self.condition:
            pending=None
            while True:
                self.provider.check()
                if self.steer_pending or (pending and pending.get('superseded')):return False
                if pending and pending['decision'] is False:
                    self.set_activity('stopped','Change declined · send a message to continue')
                    self.emit('info',text='Step declined. Scene unchanged by this step.');return False
                if self.config.get('apply_mode','manual')=='auto' or (pending and pending['decision'] is True):
                    self.pending=None
                    self.phase='python'
                    self.send({'type':'approval','id':'','message':''})
                    return True
                if pending is None:
                    pending={'id':uuid.uuid4().hex,'code':step['code'],'message':step['message'],'decision':None}
                    self.pending=pending;self.phase='approval'
                    self.set_activity('approval')
                    self.emit('approval',approval={k:v for k,v in pending.items() if k!='decision'})
                    self.send({'type':'approval','id':pending['id'],'message':step['message'],'code':step['code']})
                    self.native_state()
                self.condition.wait(.2)

    def run(self, config, document=None):
        completed=False
        advance_after_failure=False
        try:
            scene = self.command('inspect',prepare_edit=True)
            if not scene.get('ok'):
                raise RuntimeError(scene.get('error', 'Cannot inspect Cinema 4D'))
            if document and scene['scene'].get('document')!=document:
                raise RuntimeError('This task belongs to another document. Return to that document and resend the task; remaining tasks are paused.')
            with self.lock:
                self.scene=scene['scene']
                unbound=[item for item in self.queue if not item.get('document')]
                for item in unbound:item['document']=scene['scene'].get('document')
                if unbound:self.save_queue()
            self.history.append({'role': 'tool', 'content': scene})
            active_goal=self.goal if self.goal and self.goal['status']=='active' else None
            if active_goal:
                if active_goal.get('document') and active_goal['document']!=scene['scene']['document']:
                    raise RuntimeError('Goal belongs to a different document. Review the scene and explicitly continue the goal.')
                active_goal['document']=scene['scene']['document']
            errors = 0
            self.activity["repair_attempt"]=0
            limit=max(0,active_goal['budget']-active_goal['steps']) if active_goal else 24
            for index in range(limit):
                if active_goal:
                    active_goal['steps']+=1
                    self.save_goal()
                with self.lock:
                    if self.steer_pending:return
                    self.phase='idle'
                self.provider.check()
                self.emit('status', text=f'{config["provider"]} · step {index + 1}')
                planned_scene=next((item['content']['scene'] for item in reversed(self.history)
                                    if item.get('role')=='tool' and isinstance(item.get('content'),dict)
                                    and isinstance(item['content'].get('scene'),dict)),None)
                with self.lock:
                    if self.steer_pending:return
                    self.phase='model'
                    self.set_activity('waiting')
                self.emit('step_start')
                try:
                    step = self.provider.step(config, self.history)
                except SyntaxError as exc:
                    self.provider.check()
                    errors+=1
                    result={'operation':'validation','ok':False,'error':f'SyntaxError: {exc.msg}',
                            'error_line':exc.lineno,'code_context':(exc.text or '')[:1500],
                            'executed':False,'api_hint':'Fix the Python syntax before returning another step. No code was executed.'}
                    self.history.append({'role':'tool','content':result})
                    self.emit('tool',result=result)
                    if errors>3:raise SceneStepFailed('This task still contains invalid Python after 3 repair attempts. Continuing with the next queued request, if any.')
                    self.activity['repair_attempt']=errors
                    self.set_activity('repairing',result['error'])
                    continue
                finally:
                    self.emit('step_end')
                with self.lock:
                    self.phase='idle'
                    if self.steer_pending:return
                self.history.append({'role': 'assistant', 'content': step})
                if step['action']=='done' and errors:
                    raise SceneStepFailed('The last Cinema 4D step failed and was not repaired. Continuing with the next queued request, if any.')
                self.emit('assistant', text=step['message'])
                if step['action'] == 'done':
                    completed=True
                    self.set_activity('done')
                    if active_goal:
                        active_goal['status']='review'
                        self.save_goal()
                        self.emit('info',text='Goal result is ready for your review. Mark complete or continue.')
                    return
                if step['action'] == 'recall':
                    result = recall(self.history[:-1], step['message'])
                    self.provider.check()
                elif step['action'] == 'inspect':
                    result = self.command('inspect')
                else:
                    fresh = self.command('inspect',prepare_edit=True)
                    if not fresh.get('ok'):
                        raise RuntimeError('Unable to read scene before approval')
                    if planned_scene and any(planned_scene.get(key)!=fresh['scene'].get(key) for key in ('document','revision')):
                        self.history.append({'role':'tool','content':fresh})
                        self.emit('info',text='Scene changed while the model was planning. Replanning from the current scene.')
                        continue
                    expected = fresh['scene']['revision']
                    document = fresh['scene']['document']
                    if not self.wait_for_apply(step):return
                    self.provider.check()
                    result = self.command('python', step['code'], {'revision': expected, 'document': document})
                    with self.lock:
                        self.pending = None
                    self.send({'type': 'approval', 'id': '', 'message': ''})
                self.history.append({'role': 'tool', 'content': result})
                self.emit('tool', result=result)
                if not result.get('ok'):
                    if step['action']!='python' or result.get('failure_kind')=='precondition':
                        raise RuntimeError(result.get('error','Cinema 4D could not prepare the scene step'))
                    errors += 1
                    if errors > 3:
                        raise SceneStepFailed('This task could not be repaired after 3 attempts. The failed step may be partially applied; the next queued request will inspect the scene first.')
                    if not isinstance(result.get('scene'),dict):
                        measured=self.command('inspect')
                        if not measured.get('ok'):raise RuntimeError('Cannot read the scene after a failed step')
                        self.history.append({'role':'tool','content':measured})
                    self.activity['repair_attempt']=errors
                    self.set_activity('repairing',result.get('error','Repairing the failed Python step'))
                    self.emit('info',text=f'Repairing the failed step · attempt {errors}/3. Existing scene changes are preserved.')
                elif step['action']=='python':
                    errors=0
                    self.activity['repair_attempt']=0
            raise RuntimeError('Step budget reached. Review the scene and explicitly continue with more steps.')
        except SceneStepFailed as exc:
            self.history.append({'role':'tool','content':{'operation':'task','ok':False,'status':'failed','error':str(exc)}})
            advance_after_failure=True
            self.set_activity('error',str(exc))
            self.emit('error',text=str(exc))
        except Cancelled:
            if not self.uncertain:self.set_activity('updating' if self.steer_pending else 'stopped', '' if self.steer_pending else 'Stopped by you · send a message to continue')
            if not self.steer_pending:self.emit('info', text='Stopped. Future steps cancelled. A Python operation already running in Cinema 4D cannot be forcibly interrupted.')
        except Exception as exc:
            if not self.uncertain:self.set_activity('error',str(exc))
            self.emit('error', text=str(exc))
        finally:
            with self.lock:
                self.pending = None
                self.phase='idle'
                self.busy = False
                if self.connected and not self.uncertain:
                    try:(self.data/'active-task.json').unlink()
                    except FileNotFoundError:pass
                if self.steer_pending:completed=True
                if not completed and not advance_after_failure:self.queue_paused=True
                if self.uncertain:self.set_activity('uncertain','Waiting for Cinema 4D to acknowledge the last operation')
                if self.goal and self.goal['status']=='active' and not self.steer_pending:
                    self.goal['status']='paused'
                    self.save_goal()
                try:
                    self.send({'type':'approval','id':'','message':''})
                    self.emit('status',text='Queue paused' if self.queue and self.queue_paused else ('Waiting for Cinema 4D to acknowledge the last operation' if self.uncertain else 'Ready'))
                    self.start_next()
                    self.native_state()
                except OSError:
                    self.connected=False

    def state(self, after=0):
        with self.lock:
            return {'events': [e for e in self.events if e['id'] > after], 'busy': self.busy or bool(self.uncertain),
                    'activity':dict(self.activity),
                    'queue_count':len(self.queue),'queue_paused':self.queue_paused,
                    'connected': self.connected, 'scene': self.scene, 'config': self.config,'goal':self.goal,
                    'pending': {k: v for k, v in self.pending.items() if k != 'decision'} if self.pending and self.pending['decision'] is None else None}

    def discover_models(self, config):
        key=config.get('provider','')
        with self.catalog_lock:
            if key in self.catalog_active:return
            self.catalog_active.add(key)
        provider=Providers(self.data/'runtime',lambda *args,**kwargs:None)
        with self.catalog_lock:self.catalog_providers.add(provider)
        try:
            models=provider.models(config)
            self.send({'type':'models','provider':config['provider'],'models':models,'error':''})
        except Exception as exc:
            self.send({'type':'models','provider':config.get('provider',''),'models':[], 'error':str(exc)[:500]})
        finally:
            provider.stop()
            with self.catalog_lock:
                self.catalog_active.discard(key)
                self.catalog_providers.discard(provider)

    def settings(self, value):
        if not isinstance(value, dict):
            raise ValueError('Settings must be an object')
        with self.lock:
            if (self.busy or self.uncertain) and any(k!='apply_mode' and v!=self.config.get(k) for k,v in value.items()):
                raise ValueError('Wait for the current task before changing providers')
            if value.get('provider',self.config['provider']) not in ('codex', 'claude', 'lmstudio'):
                raise ValueError('Unknown provider')
            if any(not isinstance(v, str) or len(v) > 2048 for v in value.values()):
                raise ValueError('Invalid settings')
            local_base(value.get('endpoint', self.config['endpoint']))
            if value.get('effort','') not in ('','off','on','none','minimal','low','medium','high','xhigh','max','ultra'):
                raise ValueError('Unsupported reasoning effort')
            if not value.get('chat_rows',self.config['chat_rows']).isdigit() or not 6<=int(value.get('chat_rows',self.config['chat_rows']))<=60:
                raise ValueError('Chat height must be between 6 and 60 rows')
            if value.get('apply_mode',self.config['apply_mode']) not in ('manual','auto'):
                raise ValueError('Unknown apply mode')
            updated={**self.config,**{k: v for k, v in value.items() if k in self.config}}
            if updated!=self.config:
                atomic_json(self.data/'settings.json',updated)
                self.config=updated
            self.condition.notify_all()

    def save_chat(self):
        self.store.save(self.chat_id,self.history,list(self.saved_events),self.goal,self.draft)

    def chat_title(self):
        return title([{'kind':'user','text':h['content']} for h in self.history if h.get('role')=='user'] or self.saved_events)

    def restore_chat(self,ident,value=None):
        if value is None:value=self.store.load(ident)
        self.chat_id=ident;self.history=value['history'];self.draft=value.get('draft',{})
        self.activity={'state':'ready','since':time.time(),'task_started':0,'updated':0,'finished':0,'reason':''}
        self.events=collections.deque(value['events'],maxlen=1500)
        self.saved_events=collections.deque(value['events'],maxlen=1500);self.thoughts={}
        self.sequence=max([self.sequence]+[e.get('id',0) for e in self.events])
        self.goal=value.get('goal')
        if self.goal and self.goal.get('status')=='active':self.goal['status']='paused'

    def send_chats(self):
        with self.lock:
            self.send({'type':'chats','chats':self.store.catalog(),'active':self.chat_id})

    def publish_chat(self):
        if not self.native_ready:return
        self.send({'type':'chat_opened','id':self.chat_id,'draft':self.draft})
        for event in self.saved_events:self.send({'type':'event','event':event})
        self.native_state();self.send_chats()

    def check_chat_switch(self):
        if self.busy or self.uncertain or self.queue:
            raise ValueError('Finish the current task and queued messages before switching chats')

    def capture_draft(self,draft):
        if draft is None:return
        if not isinstance(draft,dict) or not isinstance(draft.get('text',''),str) or len(draft.get('text',''))>16000:
            raise ValueError('Invalid chat draft')
        paths=draft.get('attachments',[])
        if not isinstance(paths,list) or len(paths)>6 or any(not isinstance(p,str) or len(p)>4096 for p in paths):raise ValueError('Invalid draft attachments')
        self.draft={'text':draft.get('text',''),'attachments':paths,'mode':draft.get('mode','CHAT') if draft.get('mode') in ('CHAT','GOAL') else 'CHAT'}

    def switch_chat(self,ident,draft=None):
        with self.lock:
            self.check_chat_switch()
            if ident==self.chat_id:
                self.capture_draft(draft);self.save_chat();return
            # Read first: a missing/corrupt destination must leave the current chat intact.
            value=self.store.load(ident)
            self.capture_draft(draft);self.save_chat()
            self.restore_chat(ident,value)
            self.pending=None;self.steer_pending=None
            self.save_goal(notify=False);self.save_chat();self.publish_chat()

    def new_chat(self,draft=None):
        with self.lock:
            self.check_chat_switch()
            self.capture_draft(draft);self.save_chat()
            self.history=[];self.chat_id=uuid.uuid4().hex;self.events.clear();self.saved_events.clear();self.thoughts={}
            self.goal=None;self.draft={};self.pending=None
            self.activity={'state':'ready','since':time.time(),'task_started':0,'updated':0,'finished':0,'reason':''}
            self.save_goal(notify=False)
            self.emit('info',text='New chat. Switch chats using Chats in the header.')
            self.publish_chat()



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    token = sys.stdin.readline().strip()
    if len(token) < 32:raise SystemExit('Missing session secret')
    bridge = socket.create_connection(('127.0.0.1', args.port), timeout=10)
    bridge.settimeout(None)
    session = Session(bridge, token, args.data)
    session.send({'type':'hello','version':VERSION,'token':token})
    session.native_ready=True
    session.publish_chat()
    def terminate(signum, frame):raise SystemExit(0)
    signal.signal(signal.SIGTERM,terminate)
    try:session.bridge_reader()
    finally:
        session.connected=False
        session.provider.stop()
        with session.catalog_lock:providers=list(session.catalog_providers)
        for provider in providers:provider.stop()
        bridge.close()

if __name__ == '__main__':main()
