"""Run inside Cinema's Python console. Creates a separate validation document."""
import c4d
import importlib
import json
from pathlib import Path
import sys
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugin'/'LLMLINK'))
import llmlink_c4d.scene_tools
importlib.reload(llmlink_c4d.scene_tools)
from llmlink_c4d.scene_tools import SceneExecutor,snapshot,node_state,parameter_value,objects

def state_details(doc):
    return {'objects':[node_state(obj) for obj in objects(doc)],'materials':[node_state(mat) for mat in doc.GetMaterials()],'render':parameter_value(doc.GetActiveRenderData().GetDataInstance())}

class NativeWorkflow(c4d.gui.GeDialog):
    def CreateLayout(self):
        self.SetTitle('LLMLINK · Integration check')
        self.AddStaticText(100,c4d.BFH_SCALEFIT,name='Testing scene, render and file integrity…')
        return True
    def InitValues(self):
        self.results=[]
        self.doc=c4d.documents.BaseDocument()
        self.doc.SetDocumentName('LLMLINK-Validation.c4d')
        c4d.documents.InsertBaseDocument(self.doc)
        c4d.documents.SetActiveDocument(self.doc)
        self.executor=SceneExecutor(ROOT/'tests'/'native-output')
        self.phase=0
        self.SetTimer(100)
        return True
    def run_code(self,code,expected=None):
        if expected is None:
            scene=snapshot(self.doc);expected={k:scene[k] for k in ('document','revision')}
        return self.executor.execute({'id':uuid.uuid4().hex,'operation':'python','code':code,'expected':expected,'expires_at':time.time()+85})
    def Timer(self,msg):
        try:
            if self.phase==0:
                stale=self.run_code('raise RuntimeError("must not run")',{'document':'wrong','revision':'wrong'})
                assert stale['failure_kind']=='precondition' and not stale['executed']
                self.results.append({'test':'stale scene rejected','ok':True})
                code='''
body=c4d.BaseObject(c4d.Ocube)
body.SetName('LLMLINK Test Cube')
body[c4d.PRIM_CUBE_LEN]=c4d.Vector(160,160,160)
body.SetAbsPos(c4d.Vector(0,80,0))
doc.InsertObject(body)
doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,body)
mat=c4d.BaseMaterial(c4d.Mmaterial)
mat.SetName('LLMLINK Blue')
mat[c4d.MATERIAL_COLOR_COLOR]=c4d.Vector(0.08,0.32,0.8)
doc.InsertMaterial(mat)
tag=c4d.TextureTag();tag.SetMaterial(mat);body.InsertTag(tag)
floor=c4d.BaseObject(c4d.Oplane);floor.SetName('LLMLINK Floor')
floor[c4d.PRIM_PLANE_WIDTH]=1200;floor[c4d.PRIM_PLANE_HEIGHT]=1200
doc.InsertObject(floor);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,floor)
camera=c4d.BaseObject(c4d.Ocamera);camera.SetName('LLMLINK Camera')
camera.SetAbsPos(c4d.Vector(400,280,-600))
camera.SetAbsRot(c4d.utils.VectorToHPB(c4d.Vector(0,80,0)-camera.GetAbsPos()))
doc.InsertObject(camera);doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,camera)
doc.GetActiveBaseDraw().SetSceneCamera(camera)
light=c4d.BaseObject(c4d.Olight);light.SetName('LLMLINK Light')
light.SetAbsPos(c4d.Vector(-300,600,-400));doc.InsertObject(light)
doc.AddUndo(c4d.UNDOTYPE_NEWOBJ,light)
llmlink.set_key(body,c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION,c4d.DTYPE_VECTOR,0),c4d.DescLevel(c4d.VECTOR_X,c4d.DTYPE_REAL,0)),0,0)
llmlink.set_key(body,c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_POSITION,c4d.DTYPE_VECTOR,0),c4d.DescLevel(c4d.VECTOR_X,c4d.DTYPE_REAL,0)),30,120)
print('Created',len(llmlink.objects()),'objects and',len(doc.GetMaterials()),'material')
'''
                result=self.run_code(code);self.results.append({'test':'objects materials animation checkpoint','result':result})
                assert result['ok'],result
                assert result['scene']['object_count']==4
                self.early_revision=result['scene']['revision']
                self.before_debug=state_details(self.doc)
                assert Path(result['checkpoint']).is_file()
                saved=self.run_code("llmlink.save_scene('LLMLINK-Validation')")
                assert saved['ok'],saved
                self.saved=saved['files'][0]
                self.results.append({'test':'save scene copy','result':saved})
                self.phase=1
                result=self.run_code("llmlink.render(width=320,height=240,engine='standard',name='LLMLINK-Validation')")
                assert result is None,result
            elif self.phase==1:
                result=self.executor.poll_render()
                if result is None:return
                self.results.append({'test':'async render PNG','result':result})
                assert result['ok'],result
                loaded=c4d.documents.LoadDocument(self.saved,c4d.SCENEFILTER_OBJECTS|c4d.SCENEFILTER_MATERIALS,None)
                assert loaded is not None
                measured=snapshot(loaded)
                assert measured['object_count']==4,measured
                assert len(measured['materials'])==1,measured
                assert next(o for o in measured['objects'] if o['name']=='LLMLINK Test Cube')['animation'][0]['keys']==2,measured
                self.results.append({'test':'saved file reopened','ok':True,'scene':measured})
                self.stable=snapshot(self.doc)['revision']
                (ROOT/'tests/revision-debug-result.json').write_text(json.dumps({'before':self.before_debug,'after':state_details(self.doc)},ensure_ascii=False,indent=2))
                assert self.stable==self.early_revision,'Deferred editor updates changed the revision'
                self.stability_ticks=0
                self.phase=2
            elif self.phase==2:
                self.stability_ticks+=1
                if self.stability_ticks<10:return
                assert snapshot(self.doc)['revision']==self.stable,'Revision changed while idle'
                obj=self.doc.SearchObject('LLMLINK Test Cube')
                obj.SetAbsPos(c4d.Vector(10,80,0))
                assert snapshot(self.doc)['revision']!=self.stable,'Real edit was not detected'
                self.results.append({'test':'idle revision stable and real edit detected','ok':True})
                self.finish(True)
        except Exception:
            self.results.append({'error':traceback.format_exc()})
            self.finish(False)
    def finish(self,ok):
        self.SetTimer(0)
        self.phase=3
        report={'ok':ok,'version':c4d.GetC4DVersion(),'results':self.results}
        (ROOT/'tests'/'c4d-native-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        self.SetString(100,'Passed · scene, animation, render and save/reopen' if ok else 'Failed · see c4d-native-result.json')
        print('LLMLINK NATIVE TEST',ok)

global llmlink_native_workflow
llmlink_native_workflow=NativeWorkflow()
llmlink_native_workflow.Open(c4d.DLG_TYPE_ASYNC,defaultw=480,defaulth=100)
