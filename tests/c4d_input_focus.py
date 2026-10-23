"""Run in Cinema 4D via runpy to verify composer focus during streaming updates."""
import hashlib
import json
from pathlib import Path
from llmlink_c4d import dialog as ui
ROOT=Path(__file__).resolve().parents[1]
source=Path(ui.__file__)
p=ui.ACTIVE_DIALOG
saved={'events':p.events,'stream':p.stream,'thoughts':p.GetBool(ui.THOUGHTS),'draft':p.GetString(ui.COMPOSER)}
report={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'checks':[],'ok':False}
try:
 p.Activate(ui.COMPOSER)
 p.redraw_history()
 assert p.IsActive(ui.COMPOSER),'Composer lost focus on history refresh'
 report['checks'].append('Updated history preserves native composer focus')
 p.SetBool(ui.THOUGHTS,True)
 p.stream=dict(p.stream)
 for n in range(40):
  p.SetString(ui.COMPOSER,'Следующее задание '+str(n))
  p.stream['reasoning']='Live focus regression '+str(n)
  p.redraw_history()
  assert p.IsActive(ui.COMPOSER),str(n)
  assert p.GetString(ui.COMPOSER)=='Следующее задание '+str(n),str(n)
 report['checks'].append('40 visible streaming updates preserve native typing focus and draft')
 p.SetBool(ui.THOUGHTS,False)
 for n in range(40):
  p.stream['reasoning']='Hidden reasoning '+str(n)
  p.redraw_history()
  assert p.IsActive(ui.COMPOSER),str(n)
 report['checks'].append('40 hidden streaming updates preserve native typing focus')
 current=dict(p.state)
 p.receive({**current,'type':'state','busy':True})
 assert p.IsActive(ui.COMPOSER),'Busy state took composer focus'
 report['checks'].append('Busy state leaves composer editable and focused')
 p.receive({**current,'type':'state'})
 report['ok']=True
finally:
 p.events=saved['events'];p.stream=saved['stream'];p.SetBool(ui.THOUGHTS,saved['thoughts'])
 p.SetString(ui.COMPOSER,saved['draft'])
 p.history_text=None;p.Activate(ui.COMPOSER);p.redraw_history()
 (ROOT/'tests/input-focus-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(report)
