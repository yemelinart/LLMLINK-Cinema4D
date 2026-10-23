import json,socket,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugin'/'LLMLINK'/'llmlink_c4d'))
from companion import Session
from chat_store import ChatStore

class ChatHistoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.a,self.b=socket.socketpair();self.s=Session(self.a,'x'*40,self.tmp.name)
 def tearDown(self):self.a.close();self.b.close();self.tmp.cleanup()
 def fill(self,text='Build a house'):
  self.s.history=[{'role':'user','content':text,'attachments':[{'kind':'text','name':'notes','text':'Blue roof'}]}]
  self.s.emit('user',text=text,attachments=[{'kind':'text','name':'notes'}])
  self.s.history.append({'role':'assistant','content':{'action':'done','message':'Ready','code':''}})
  self.s.emit('assistant',text='Ready')
 def test_round_trip_drafts_attachments_goals_and_thoughts(self):
  self.fill();old=self.s.chat_id
  self.s.goal={'status':'review','text':'House','steps':2,'budget':5}
  self.s.emit('step_start');self.s.emit('stream',channel='reasoning',text='Visible summary');self.s.emit('step_end')
  self.s.new_chat({'text':'Add a garden','attachments':['/tmp/image.png'],'mode':'CHAT'})
  new=self.s.chat_id;self.assertNotEqual(old,new);self.assertIsNone(self.s.goal);self.assertEqual(self.s.history,[])
  self.s.switch_chat(old,{'text':'Second chat draft'})
  self.assertEqual(self.s.history[0]['attachments'][0]['text'],'Blue roof')
  self.assertEqual(self.s.draft['text'],'Add a garden');self.assertEqual(self.s.goal['status'],'review')
  self.assertTrue(any(e['kind']=='thought' and e['text']=='Visible summary' for e in self.s.saved_events))
  self.s.switch_chat(new);self.assertEqual(self.s.draft['text'],'Second chat draft')
 def test_selecting_same_chat_keeps_current_draft(self):
  self.fill();self.s.switch_chat(self.s.chat_id,{'text':'Newest draft'})
  self.assertEqual(self.s.draft['text'],'Newest draft');self.assertEqual(len(self.s.history),2)
 def test_connection_error_is_not_replayed_after_restart(self):
  self.fill();self.s.emit('error',text='Disconnected',transient=True)
  other=Session(self.a,'x'*40,self.tmp.name)
  self.assertFalse(any(e.get('text')=='Disconnected' for e in other.saved_events))
 def test_restart_reopens_last_chat_without_running_commands(self):
  self.fill();old=self.s.chat_id;self.s.new_chat();second=self.s.chat_id
  self.s.switch_chat(old)
  other=Session(self.a,'x'*40,self.tmp.name)
  self.assertEqual(other.chat_id,old);self.assertEqual(other.history,self.s.history)
  self.assertFalse(other.busy);self.assertFalse(other.queue);self.assertIsNone(other.pending)
  self.assertEqual({c['id'] for c in other.store.catalog()},{old,second})
 def test_busy_queue_and_unknown_operation_protect_active_chat(self):
  self.fill();old=self.s.chat_id;self.s.new_chat();new=self.s.chat_id
  for field,value in [('busy',True),('uncertain','command'),('queue',[{'id':'pending'}])]:
   setattr(self.s,field,value)
   with self.assertRaises(ValueError):self.s.switch_chat(old)
   with self.assertRaises(ValueError):self.s.new_chat()
   self.assertEqual(self.s.chat_id,new)
   setattr(self.s,field,False if field=='busy' else (None if field=='uncertain' else []))
 def test_invalid_or_missing_destination_preserves_current_chat(self):
  self.fill();old=self.s.chat_id
  for ident in ['../escape','f'*32]:
   with self.assertRaises((ValueError,OSError)):self.s.switch_chat(ident,{'text':'Keep draft'})
   self.assertEqual(self.s.chat_id,old);self.assertEqual(len(self.s.history),2)
 def test_earlier_jsonl_chat_imports_without_replaying_code(self):
  ident='a'*32;p=Path(self.tmp.name)/('chat-'+ident+'.jsonl')
  events=[{'kind':'user','id':1,'text':'Earlier house'}, {'kind':'assistant','id':2,'text':'Created'},
          {'kind':'approval','id':3,'approval':{'code':"raise Exception('must never execute')"}},
          {'kind':'tool','id':4,'result':{'operation':'python','ok':True,'output':'Done'}}]
  p.write_text('\n'.join(json.dumps(e) for e in events)+'\n{"partial"')
  self.assertIn(ident,[c['id'] for c in self.s.store.catalog()])
  self.s.switch_chat(ident)
  self.assertEqual(self.s.history[0]['content'],'Earlier house');self.assertFalse(self.s.busy)
  self.assertIsNone(self.s.pending);self.assertEqual(len(self.s.history),3)
 def test_continuation_gets_restored_context_and_fresh_inspection(self):
  self.fill();old=self.s.chat_id;self.s.new_chat();self.s.switch_chat(old)
  commands=[];seen=[]
  self.s.command=lambda operation,*a,**k:(commands.append(operation) or {'ok':True,'scene':{'document':'current','revision':7}})
  def step(config,history):
   seen.extend(history)
   return {'action':'done','message':'Continued','code':''}
  self.s.provider.step=step;self.s.chat('Continue the house')
  end=time.monotonic()+3
  while self.s.busy and time.monotonic()<end:time.sleep(.01)
  self.assertFalse(self.s.busy);self.assertEqual(commands,['inspect'])
  self.assertEqual(seen[0]['content'],'Build a house')
  self.assertEqual(seen[-1]['content']['scene']['document'],'current')
 def test_title_survives_many_stream_chunks_and_private_files(self):
  self.fill('Original title')
  for n in range(1600):self.s.emit('stream',text='x')
  self.s.emit('step_end')
  item=next(c for c in self.s.store.catalog() if c['id']==self.s.chat_id)
  self.assertEqual(item['title'],'Original title')
  self.assertTrue(any(e['kind']=='user' for e in self.s.store.load(self.s.chat_id)['events']))
  self.assertEqual((Path(self.tmp.name)/'chats'/(self.s.chat_id+'.json')).stat().st_mode&0o777,0o600)
