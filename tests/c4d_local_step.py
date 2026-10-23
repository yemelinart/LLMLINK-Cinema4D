"""Minimal real LM Studio execution proof on the installed native panel."""
import c4d,json,time,traceback,uuid
from pathlib import Path
from llmlink_c4d import dialog as ui
from llmlink_c4d.scene_tools import snapshot,save_copy
ROOT=Path(__file__).resolve().parents[1]
class LocalStep(c4d.gui.GeDialog):
 def CreateLayout(self):
  self.SetTitle('LLMLINK · Local model check');self.AddStaticText(800,c4d.BFH_SCALEFIT,name='Checking one local model scene step…');return True
 def InitValues(self):
  self.phase=0;self.start=time.monotonic();self.done=False
  doc=c4d.documents.LoadDocument(str(ROOT/'examples/Codex-Demo.c4d'),c4d.SCENEFILTER_OBJECTS|c4d.SCENEFILTER_MATERIALS,None)
  c4d.documents.InsertBaseDocument(doc);c4d.documents.SetActiveDocument(doc);doc.SetDocumentName('LLMLINK-Local-Demo.c4d')
  self.before=snapshot(doc)['object_count'];self.name='LLMLINK Local Verified '+uuid.uuid4().hex[:4]
  c4d.CallCommand(ui.PLUGIN_ID);self.panel=ui.ACTIVE_DIALOG;self.SetTimer(100);return True
 def finish(self,ok,error=''):
  self.done=True;self.SetTimer(0)
  value={'ok':ok,'elapsed':time.monotonic()-self.start,'name':self.name,'error':error,'scene':snapshot(c4d.documents.GetActiveDocument())}
  if ok:value['saved_scene']=save_copy(c4d.documents.GetActiveDocument(),ROOT/'examples/LM-Studio-Demo.c4d')
  (ROOT/'tests/local-step-result.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
  self.panel.send({'type':'stop'})
  self.SetString(800,'Passed · local model created a verified Cinema 4D cube' if ok else 'Failed · see local-step-result.json')
 def Timer(self,msg):
  if self.done:return
  p=self.panel
  try:
   if p.bridge.failed:raise RuntimeError(p.GetString(ui.STATUS))
   if time.monotonic()-self.start>300:raise TimeoutError('Single local step exceeded five minutes')
   if self.phase==0 and p.bridge.authenticated:
    self.old=p.state.get('chat_id');p.send({'type':'new_chat','draft':{'text':'','attachments':[],'mode':'CHAT'}});self.phase=1
   elif self.phase==1 and p.state.get('chat_id')!=self.old:
    p.SetInt32(ui.PROVIDER,1);p.Command(ui.PROVIDER,c4d.BaseContainer());p.connect();self.phase=2
   elif self.phase==2 and p.models and p.config.get('provider')=='lmstudio':
    index=next(i for i,m in enumerate(p.models) if m['id']=='qwen3.8-27b-uncensored-mlx')
    p.SetInt32(ui.MODEL,index);p.update_efforts();p.SetInt32(ui.MODE,1);p.Command(ui.MODE,c4d.BaseContainer())
    self.seq=max([e.get('id',0) for e in p.events] or [0])
    code='obj = c4d.BaseObject(c4d.Ocube)\nobj.SetName('+repr(self.name)+')\ndoc.InsertObject(obj)\ndoc.AddUndo(c4d.UNDOTYPE_NEWOBJ, obj)\nprint(obj.GetName())'
    p.SetString(ui.COMPOSER,'Run this exact short Cinema 4D Python as one python action. Do not add lookups, transforms, or other operations:\n'+code+'\nAfter its successful tool result, return done.')
    p.Command(ui.SEND,c4d.BaseContainer());self.phase=3
   elif self.phase==3:
    results=[e['result'] for e in p.events if e.get('id',0)>self.seq and e.get('kind')=='tool' and e['result'].get('operation')=='python']
    if results and results[-1].get('ok'):
     scene=snapshot(c4d.documents.GetActiveDocument());matches=[o for o in scene['objects'] if o['name']==self.name]
     assert len(matches)==1 and scene['object_count']==self.before+1,(matches,scene)
     assert matches[0]['type']==c4d.Ocube and matches[0]['dimensions']==[200.0,200.0,200.0],matches
     self.finish(True)
    elif any(e.get('id',0)>self.seq and e.get('kind')=='error' for e in p.events):raise RuntimeError('Local model task failed')
  except Exception:self.finish(False,traceback.format_exc())
global llmlink_local_check
llmlink_local_check=LocalStep();llmlink_local_check.Open(c4d.DLG_TYPE_ASYNC,defaultw=510,defaulth=100)
