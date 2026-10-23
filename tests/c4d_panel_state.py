"""Verify native panel state recreation and leave the installed plugin ready in Manual mode."""
import c4d,json,time,traceback
from pathlib import Path
from llmlink_c4d import dialog as ui
ROOT=Path(__file__).resolve().parents[1]
class PanelState(c4d.gui.GeDialog):
 def CreateLayout(self):
  self.SetTitle('LLMLINK · Panel state check');self.AddStaticText(900,c4d.BFH_SCALEFIT,name='Checking panel restoration…');return True
 def InitValues(self):
  self.phase=0;self.started=time.monotonic();self.checks=[];c4d.CallCommand(ui.PLUGIN_ID);self.panel=ui.ACTIVE_DIALOG;self.SetTimer(100);return True
 def Timer(self,msg):
  p=self.panel
  try:
   if time.monotonic()-self.started>30:raise TimeoutError('Panel state test timed out')
   if self.phase==0 and p.bridge.authenticated:
    p.SetInt32(ui.PROVIDER,1);p.Command(ui.PROVIDER,c4d.BaseContainer());p.connect();self.phase=1
   elif self.phase==1 and p.models and p.config.get('provider')=='lmstudio':
    i=next(i for i,m in enumerate(p.models) if m['id']=='qwen3.8-27b-uncensored-mlx')
    p.SetInt32(ui.MODEL,i);p.update_efforts();p.SetInt32(ui.MODE,0);p.Command(ui.MODE,c4d.BaseContainer())
    p.send({'type':'settings','config':p.selected_config()});self.after=time.monotonic();self.phase=2
   elif self.phase==2 and time.monotonic()-self.after>.5:
    p.InitValues();self.after=time.monotonic();self.phase=3
   elif self.phase==3 and time.monotonic()-self.after>.5:
    assert p.GetInt32(ui.PROVIDER)==1,p.config
    assert p.GetInt32(ui.MODE)==0,p.config
    assert p.models[p.GetInt32(ui.MODEL)]['id']=='qwen3.8-27b-uncensored-mlx',p.models
    self.checks.append('Provider, model and Manual mode survive native panel reinitialization')
    p.SetInt32(ui.PROVIDER,0);p.Command(ui.PROVIDER,c4d.BaseContainer());p.connect();self.phase=4
   elif self.phase==4 and p.models and p.config.get('provider')=='codex':
    i=next(i for i,m in enumerate(p.models) if m['id']=='gpt-6.1-sol');p.SetInt32(ui.MODEL,i);p.update_efforts()
    p.SetInt32(ui.EFFORT,p.efforts.index('low'));p.SetInt32(ui.MODE,0);p.Command(ui.MODE,c4d.BaseContainer())
    p.send({'type':'settings','config':p.selected_config()});self.old=p.state.get('chat_id')
    p.send({'type':'new_chat','draft':{'text':'','attachments':[],'mode':'CHAT'}});self.phase=5
   elif self.phase==5 and p.state.get('chat_id')!=self.old:
    assert p.config['provider']=='codex' and p.config['apply_mode']=='manual',p.config
    self.checks.append('Installed panel ready with Codex, selected model, low thinking and Manual apply')
    self.finish(True)
  except Exception:self.finish(False,traceback.format_exc())
 def finish(self,ok,error=''):
  self.SetTimer(0);self.phase=6
  (ROOT/'tests/panel-state-result.json').write_text(json.dumps({'ok':ok,'checks':self.checks,'error':error,'config':self.panel.config},ensure_ascii=False,indent=2))
  self.SetString(900,'Passed · LLMLINK is ready in Manual mode' if ok else 'Failed · see panel-state-result.json')
global llmlink_panel_state
llmlink_panel_state=PanelState();llmlink_panel_state.Open(c4d.DLG_TYPE_ASYNC,defaultw=500,defaulth=100)
