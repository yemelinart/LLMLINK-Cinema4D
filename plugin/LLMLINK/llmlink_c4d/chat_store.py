"""Private, atomic chat snapshots and read-only migration of earlier event logs."""
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import time
from persistence import atomic_json

ID=re.compile(r'[0-9a-f]{32}\Z')

def valid_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError('Invalid chat ID')
    return value

def title(events):
    first=next((e.get('text','') for e in events if e.get('kind')=='user'),'New chat')
    return ' '.join(first.split())[:70] or 'New chat'

class ChatStore:
    def __init__(self,data):
        self.data=Path(data);self.root=self.data/'chats'
        self.root.mkdir(exist_ok=True,mode=0o700);self.root.chmod(0o700)

    def atomic(self,path,value):
        atomic_json(path, value)

    def save_draft(self, ident, draft):
        self.atomic(self.root/(valid_id(ident)+'.draft'), draft)

    def save(self,ident,history,events,goal,draft):
        valid_id(ident)
        events=[e for e in events if e['kind']!='stream']
        self.atomic(self.root/(ident+'.json'),{'history':history,'events':events,'goal':goal,'draft':draft})
        self.save_draft(ident, draft)
        self.atomic(self.root/(ident+'.meta'),{'id':ident,'title':title([{'kind':'user','text':h['content']} for h in history if h.get('role')=='user'] or events),'updated':time.time()})
        self.atomic(self.root/'active.json',{'id':ident})

    def latest(self):
        try:
            ident=json.loads((self.root/'active.json').read_text())['id'];valid_id(ident)
            if (self.root/(ident+'.json')).is_file():return ident
        except (OSError,ValueError,KeyError,TypeError):pass
        logs=[p for p in self.data.glob('chat-*.jsonl') if ID.fullmatch(p.stem[5:])]
        return max(logs,key=lambda p:p.stat().st_mtime).stem[5:] if logs else None

    def legacy_events(self,ident):
        events=collections.deque(maxlen=1500)
        with (self.data/('chat-'+valid_id(ident)+'.jsonl')).open(encoding='utf-8') as stream:
            for line in stream:
                if len(line)>2*1024*1024:continue
                try:
                    event=json.loads(line)
                    if isinstance(event,dict) and isinstance(event.get('kind'),str):events.append(event)
                except ValueError:continue # A partial last record must not hide the rest of the chat.
        return list(events)

    def load(self,ident):
        valid_id(ident);path=self.root/(ident+'.json')
        if path.exists():
            # The model context is bounded separately. A long local archive
            # must not become impossible to reopen at an arbitrary byte count.
            with path.open(encoding='utf-8') as stream:
                value=json.load(stream)
            if not isinstance(value,dict) or not isinstance(value.get('history'),list) or not isinstance(value.get('events'),list):raise ValueError('Chat snapshot is invalid')
            if any(not isinstance(e,dict) or not isinstance(e.get('kind'),str) or not isinstance(e.get('id',0),int) for e in value['events']):raise ValueError('Chat events are invalid')
            if any(not isinstance(h,dict) or h.get('role') not in ('user','assistant','tool') or 'content' not in h for h in value['history']):raise ValueError('Chat history is invalid')
            try:
                draft=json.loads((self.root/(ident+'.draft')).read_text(encoding='utf-8'))
                if isinstance(draft,dict) and isinstance(draft.get('text',''),str) and isinstance(draft.get('attachments',[]),list):
                    value['draft']=draft
            except (OSError,ValueError):
                pass
            return value
        events=self.legacy_events(ident);history=[];users={};seen=set()
        for event in events:
            kind=event['kind'];qid=event.get('queue_id')
            if kind=='user':
                refs=[]
                for ref in event.get('attachments',[]):
                    ref=dict(ref)
                    if ref.get('kind')=='image':
                        p=Path(ref.get('path',''))
                        if not p.is_file():continue
                        ref['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
                        ref['mime']={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(p.suffix.lower(),'image/png')
                    elif ref.get('kind')=='text':
                        # Earlier logs kept metadata only; do not silently re-read edited source files.
                        ref={'name':ref.get('name','File'),'kind':'reference','source':ref.get('source',''),'note':'Earlier chat attachment; reattach to send its current contents.'}
                    refs.append(ref)
                user={'role':'user','content':event.get('text',''),'attachments':refs}
                users[qid]=user
                if not event.get('queued'):history.append(user);seen.add(qid)
            elif kind=='queue_started' and qid in users and qid not in seen:history.append(users[qid]);seen.add(qid)
            elif kind=='assistant':history.append({'role':'assistant','content':event.get('text','')})
            elif kind=='tool':history.append({'role':'tool','content':event.get('result',{})})
        return {'history':history,'events':events,'goal':None,'draft':{}}

    def catalog(self):
        items={}
        for path in self.root.glob('*.meta'):
            if not ID.fullmatch(path.stem):continue
            try:
                item=json.loads(path.read_text())
                if item['id']==path.stem and isinstance(item['title'],str) and isinstance(item['updated'],(int,float)):items[item['id']]=item
            except (OSError,ValueError,KeyError,TypeError):continue
        for path in self.data.glob('chat-*.jsonl'):
            ident=path.stem[5:]
            if ident in items or not ID.fullmatch(ident):continue
            try:
                # Find the first prompt without loading the complete log into memory.
                name='Earlier chat'
                with path.open(encoding='utf-8') as stream:
                    for _ in range(300):
                        line=stream.readline(2*1024*1024+1)
                        if not line:break
                        try:event=json.loads(line)
                        except ValueError:continue
                        if isinstance(event,dict) and event.get('kind')=='user':name=title([event]);break
                items[ident]={'id':ident,'title':name,'updated':path.stat().st_mtime}
            except OSError:continue
        return sorted(items.values(),key=lambda i:i['updated'],reverse=True)
