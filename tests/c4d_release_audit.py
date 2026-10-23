"""Run in Cinema Console. Uses a separate test document and restores the user's scene."""
import c4d
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'plugin/LLMLINK/llmlink_c4d'
spec=importlib.util.spec_from_file_location('llmlink_release_audit_'+uuid.uuid4().hex,PACKAGE/'__init__.py',submodule_search_locations=[str(PACKAGE)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
scene_module=importlib.import_module(spec.name+'.scene_tools')
SceneExecutor,snapshot,save_copy=scene_module.SceneExecutor,scene_module.snapshot,scene_module.save_copy
import llmlink_c4d.dialog as panel
assert not panel.ACTIVE_DIALOG.state.get('busy'), 'Wait for active LLMLINK work'
assert not panel.ACTIVE_DIALOG.state.get('queue_count'), 'Wait for queued LLMLINK work'
assert not panel.ACTIVE_DIALOG.bridge.executor.render, 'Wait for the current render'

class ReleaseAudit(c4d.gui.GeDialog):
 def CreateLayout(self):
  self.SetTitle('LLMLINK release verification')
  self.AddStaticText(100,c4d.BFH_SCALEFIT,name='Checking isolated document, safety, render and recovery...')
  return True
 def InitValues(self):
  self.original=c4d.documents.GetActiveDocument();self.original_state=snapshot(self.original)
  self.folder=ROOT/'tests'/'release-native-output';self.folder.mkdir(exist_ok=True)
  self.backup=save_copy(self.original,self.folder/('user-scene-preserved-'+uuid.uuid4().hex[:8]+'.c4d'))
  self.doc=c4d.documents.BaseDocument();self.doc.SetDocumentName('LLMLINK-Release-Test')
  c4d.documents.InsertBaseDocument(self.doc);c4d.documents.SetActiveDocument(self.doc)
  self.executor=SceneExecutor(self.folder);self.results=[];self.phase=0;self.SetTimer(100)
  return True
 def execute(self,code,expected=None,assets=()):
  if expected is None:expected={k:snapshot(self.doc)[k] for k in ('document','revision')}
  return self.executor.execute({'id':uuid.uuid4().hex,'operation':'python','code':code,'expected':expected,'expires_at':time.time()+85,'assets':list(assets)})
 def Timer(self,msg):
  try:
   if self.phase==0:
    before=snapshot(self.doc)
    for code in ["open('/tmp/llmlink-must-not-write','w')",'c4d.CallCommand(12099)', 'c4d.documents.KillDocument(doc)']:
     result=self.execute(code);assert not result['ok'] and not result['executed'],result
     assert snapshot(self.doc)['revision']==before['revision']
    self.results.append({'test':'unsafe Python rejected before scene mutation','ok':True})
    result=self.execute('pass',{'document':'wrong','revision':'wrong'})
    assert not result['executed'];self.results.append({'test':'stale scene rejected','ok':True})
    result=self.execute('raise SystemExit(9)')
    assert not result['ok'] and result['executed'];self.results.append({'test':'SystemExit contained; Cinema stays alive','ok':True})
    started=time.monotonic();result=self.execute('while True: pass')
    assert not result['ok'] and 'TimeoutError' in result['error'],result
    self.results.append({'test':'Python loop timeout','ok':True,'seconds':round(time.monotonic()-started,2)})
    code='''
body=c4d.BaseObject(c4d.Ocube)
body.SetName('Audit Cube');body.SetAbsPos(c4d.Vector(0,100,0))
doc.InsertObject(body);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,body)
mat=c4d.BaseMaterial(c4d.Mmaterial);mat.SetName('Audit blue')
mat[c4d.MATERIAL_COLOR_COLOR]=c4d.Vector(0.04,0.25,0.8);doc.InsertMaterial(mat)
tag=c4d.TextureTag();tag.SetMaterial(mat);body.InsertTag(tag)
plane=c4d.BaseObject(c4d.Oplane);plane[c4d.PRIM_PLANE_WIDTH]=1800;plane[c4d.PRIM_PLANE_HEIGHT]=1800
doc.InsertObject(plane);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,plane)
cam=c4d.BaseObject(c4d.Ocamera);cam.SetAbsPos(c4d.Vector(450,350,-650))
cam.SetAbsRot(c4d.utils.VectorToHPB(c4d.Vector(0,100,0)-cam.GetAbsPos()))
doc.InsertObject(cam);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,cam);doc.GetActiveBaseDraw().SetSceneCamera(cam)
light=c4d.BaseObject(c4d.Olight);light.SetAbsPos(c4d.Vector(-300,600,-450))
doc.InsertObject(light);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,light)
llmlink.set_key(body,c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION,c4d.DTYPE_VECTOR,0),c4d.DescLevel(c4d.VECTOR_X,c4d.DTYPE_REAL,0)),0,0)
llmlink.set_key(body,c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION,c4d.DTYPE_VECTOR,0),c4d.DescLevel(c4d.VECTOR_X,c4d.DTYPE_REAL,0)),30,120)
print('Verified cube, material, camera, light and animation')
'''
    result=self.execute(code);assert result['ok'],result
    assert result['scene']['object_count']==4
    assert Path(result['checkpoint']).is_file()
    self.results.append({'test':'modeling material animation checkpoint','ok':True})
    result=self.execute("llmlink.save_scene('release-check')");assert result['ok'],result
    self.saved=result['files'][0]
    assert self.execute("llmlink.render(width=320,height=240,name='release-check')") is None
    self.phase=1
   elif self.phase==1:
    result=self.executor.poll_render()
    if result is None:return
    assert result['ok'],result
    self.results.append({'test':'asynchronous PNG render','ok':True,'file':result['files'][0]})
    reopened=c4d.documents.LoadDocument(self.saved,c4d.SCENEFILTER_OBJECTS|c4d.SCENEFILTER_MATERIALS,None)
    assert reopened is not None
    measured=snapshot(reopened)
    assert measured['object_count']==4 and len(measured['materials'])==1
    assert next(o for o in measured['objects'] if o['name']=='Audit Cube')['animation'][0]['keys']==2
    self.results.append({'test':'saved scene reopened with animation and material','ok':True,'file':self.saved})
    refused=self.execute('llmlink.import_asset('+repr(self.saved)+')')
    assert not refused['ok'] and snapshot(self.doc)['object_count']==4
    imported=self.execute('llmlink.import_asset('+repr(self.saved)+')',assets=[self.saved])
    assert imported['ok'] and snapshot(self.doc)['object_count']==8,imported
    self.results.append({'test':'asset import requires explicitly attached path','ok':True})
    self.stable=snapshot(self.doc)['revision'];self.ticks=0;self.phase=2
   elif self.phase==2:
    self.ticks+=1
    # Imported animation/material state settles during Cinema's next redraw.
    if self.ticks==2:self.stable=snapshot(self.doc)['revision']
    if self.ticks<12:return
    assert snapshot(self.doc)['revision']==self.stable
    self.doc.SearchObject('Audit Cube').SetAbsPos(c4d.Vector(20,100,0))
    assert snapshot(self.doc)['revision']!=self.stable
    self.results.append({'test':'idle fingerprint stable and actual change detected','ok':True})
    self.finish(True)
  except BaseException:
   self.results.append({'error':traceback.format_exc()});self.finish(False)
 def finish(self,ok):
  self.phase=3;self.SetTimer(0);self.executor.shutdown()
  c4d.documents.SetActiveDocument(self.original)
  unchanged=snapshot(self.original)['revision']==self.original_state['revision']
  self.results.append({'test':'original user scene restored unchanged','ok':unchanged})
  report={'ok':ok and unchanged,'version':c4d.GetC4DVersion(),'results':self.results,'user_scene_backup':self.backup}
  (ROOT/'tests'/'release-native-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  c4d.documents.KillDocument(self.doc);c4d.EventAdd()
  self.SetString(100,'PASSED - user scene restored' if report['ok'] else 'FAILED - see report')
  print('LLMLINK RELEASE NATIVE',report['ok'])

release_audit=ReleaseAudit();release_audit.Open(c4d.DLG_TYPE_ASYNC,defaultw=540,defaulth=90)
