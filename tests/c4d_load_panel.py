import sys, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugin'/'LLMLINK'))
try:
    import importlib
    import llmlink_c4d.dialog
    importlib.reload(llmlink_c4d.dialog)
    from llmlink_c4d.dialog import LLMLINKDialog, PLUGIN_ID
    global llmlink_dialog
    llmlink_dialog=LLMLINKDialog()
    print('LLMLINK panel opened:',llmlink_dialog.Open(c4d.DLG_TYPE_ASYNC, 0, defaultw=560, defaulth=690))
except Exception:
    (ROOT/'tests'/'panel-error.txt').write_text(traceback.format_exc())
    traceback.print_exc()
