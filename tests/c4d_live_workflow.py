"""Drive the installed panel through its normal commands; use real signed-in providers."""
import c4d
import json
from pathlib import Path
import time
import traceback
from llmlink_c4d import dialog as ui
from llmlink_c4d.scene_tools import snapshot
ROOT=Path(__file__).resolve().parents[1]

class LiveWorkflow(c4d.gui.GeDialog):
    def CreateLayout(self):
        self.SetTitle('LLMLINK · Live verification')
        self.AddStaticText(700,c4d.BFH_SCALEFIT,name='Checking Codex queue, rendering and LM Studio…')
        return True
    def InitValues(self):
        self.phase=0;self.started=time.monotonic();self.report={'ok':False,'checks':[]};self.done=False
        doc=c4d.documents.LoadDocument(str(ROOT/'tests/recovered-codex-demo.c4d'),c4d.SCENEFILTER_OBJECTS|c4d.SCENEFILTER_MATERIALS,None)
        if doc is None:raise RuntimeError('Recovered test scene was not found')
        doc.SetDocumentName('LLMLINK-Live-Demo.c4d');c4d.documents.InsertBaseDocument(doc);c4d.documents.SetActiveDocument(doc)
        c4d.CallCommand(ui.PLUGIN_ID)
        self.panel=ui.ACTIVE_DIALOG
        self.SetTimer(100)
        return True
    def record(self,name,**data):
        self.report['checks'].append({'test':name,**data})
        self.save()
    def save(self):
        (ROOT/'tests/live-workflow-result.json').write_text(json.dumps({**self.report,'phase':self.phase,'elapsed':time.monotonic()-self.started},ensure_ascii=False,indent=2))
    def submit(self,text):
        self.panel.SetString(ui.COMPOSER,text);self.panel.Command(ui.SEND,c4d.BaseContainer())
    def Timer(self,msg):
        if self.done:return
        p=self.panel
        try:
            if p.bridge.failed:raise RuntimeError(p.GetString(ui.STATUS))
            if time.monotonic()-self.started>600:raise TimeoutError('Live provider workflow exceeded 10 minutes')
            if self.phase==0 and p.bridge.authenticated:
                self.old_chat=p.state.get('chat_id')
                p.send({'type':'new_chat','draft':{'text':'','attachments':[],'mode':'CHAT'}})
                self.phase=1
            elif self.phase==1 and p.state.get('chat_id')!=self.old_chat:
                p.SetInt32(ui.PROVIDER,0);p.config['provider']='codex';p.connect();self.phase=2
            elif self.phase==2 and p.models:
                self.record('Codex catalog',ok=True,models=p.models)
                chosen=next(i for i,m in enumerate(p.models) if m['id']=='gpt-6.1-sol')
                p.SetInt32(ui.MODEL,chosen);p.update_efforts()
                if 'low' in p.efforts:p.SetInt32(ui.EFFORT,p.efforts.index('low'))
                p.SetInt32(ui.MODE,0);p.Command(ui.MODE,c4d.BaseContainer())
                self.sequence=max([e.get('id',0) for e in p.events] or [0])
                self.submit('Сделай рендер текущей сцены 320x240 через llmlink.render(width=320,height=240,engine="standard",name="Codex-Demo"). Не изменяй объекты. Дождись успешного tool result и сообщи путь к PNG.')
                self.phase=3
            elif self.phase==3 and p.state.get('busy'):
                self.submit('Теперь отдельным шагом сохрани копию этой сцены через llmlink.save_scene("Codex-Demo"). Объекты сохрани. Сообщи путь после подтверждения сохранения.')
                self.phase=4
            elif self.phase==4 and p.pending:
                assert p.bridge.executor.render is None,'Manual mode started render before approval'
                self.record('Manual review blocks execution',ok=True,code=p.pending_code,queue_count=p.state.get('queue_count'))
                p.Command(ui.APPLY,c4d.BaseContainer())
                p.SetInt32(ui.MODE,1);p.Command(ui.MODE,c4d.BaseContainer())
                self.phase=5
            elif self.phase==5 and not p.state.get('busy') and not p.state.get('queue_count'):
                events=[e for e in p.events if e.get('id',0)>self.sequence]
                results=[e['result'] for e in events if e.get('kind')=='tool']
                files=[f for result in results for f in result.get('files',[])]
                pngs=[f for f in files if f.endswith('.png')];scenes=[f for f in files if f.endswith('.c4d')]
                if not pngs or not scenes:raise RuntimeError('Codex did not complete render and saved scene: '+str(events[-5:]))
                for file in files:assert Path(file).is_file(),file
                self.record('Codex FIFO queue renders and saves',ok=True,files=files,scene=snapshot(c4d.documents.GetActiveDocument()))
                p.SetInt32(ui.PROVIDER,1);p.Command(ui.PROVIDER,c4d.BaseContainer());p.connect()
                self.phase=6
            elif self.phase==6 and p.models and p.config.get('provider')=='lmstudio':
                chosen=next(i for i,m in enumerate(p.models) if m['id']=='qwen3.8-27b-uncensored-mlx')
                p.SetInt32(ui.MODEL,chosen);p.update_efforts();p.SetInt32(ui.EFFORT,0)
                self.record('LM Studio catalog',ok=True,models=p.models)
                self.sequence=max([e.get('id',0) for e in p.events] or [0])
                self.submit('Выполни один короткий Python-шаг: создай c4d.BaseObject(c4d.Ocube) с именем LLMLINK Local Cube, размером c4d.Vector(60,60,60), позицией c4d.Vector(-220,30,0); вставь в doc и добавь undo. Не создавай дубликат, если объект уже есть. Выведи имя объекта через print. После успешного tool result заверши задачу. Существующую сцену сохрани.')
                self.phase=7
            elif self.phase==7 and p.state.get('busy'):
                self.phase=8
            elif self.phase==8 and not p.state.get('busy'):
                scene=snapshot(c4d.documents.GetActiveDocument())
                assert any(o['name']=='LLMLINK Local Cube' for o in scene['objects']),'Local model did not create its cube'
                self.record('LM Studio edits actual Cinema scene',ok=True,scene=scene)
                self.report['ok']=True;self.phase=9;self.done=True;self.SetTimer(0);self.save()
                self.SetString(700,'Passed · Codex, LM Studio, queue, Manual/Auto and render')
        except Exception:
            self.report['error']=traceback.format_exc();self.done=True;self.SetTimer(0);self.save()
            self.SetString(700,'Failed · see live-workflow-result.json')

global llmlink_live_workflow
llmlink_live_workflow=LiveWorkflow()
llmlink_live_workflow.Open(c4d.DLG_TYPE_ASYNC,defaultw=500,defaulth=100)
