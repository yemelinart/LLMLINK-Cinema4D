import json, sys, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugin'/'LLMLINK'))
try:
    import c4d
    from llmlink_c4d.scene_tools import snapshot
    value={'ok':True,'scene':snapshot(c4d.documents.GetActiveDocument()),'python':sys.version,
           'constants':{name:getattr(c4d,name,None) for name in ['RDATA_RENDERENGINE_STANDARD','RDATA_FRAMESEQUENCE_CURRENTFRAME','RDATA_MULTIPASS_SAVEIMAGE','SAVEBIT_0','C4DPL_ENDACTIVITY']}}
except Exception:
    value={'ok':False,'error':traceback.format_exc()}
(ROOT/'tests'/'c4d-probe-result.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
print('LLMLINK PROBE',value)
