from pathlib import Path
import c4d,json,time,uuid
from llmlink_c4d.scene_tools import save_copy
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'tests/session-backups';folder.mkdir(exist_ok=True)
results=[]
document=c4d.documents.GetFirstDocument()
while document:
    following=document.GetNext()
    if document.GetDocumentName().startswith('LLMLINK-'):
        path=folder/(document.GetDocumentName().removesuffix('.c4d')+'-'+uuid.uuid4().hex[:8]+'.c4d')
        results.append(save_copy(document,path))
        c4d.documents.KillDocument(document)
    document=following
(ROOT/'tests/session-backup-result.json').write_text(json.dumps(results,indent=2))
print('LLMLINK test scene backups:',results)
