"""LLMLINK Cinema 4D plugin entrypoint. GPL-3.0-or-later."""
import os
import sys
import traceback

import c4d

ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

COMMAND = None


def PluginMessage(message_id, data):
    if message_id == c4d.C4DPL_ENDACTIVITY and COMMAND:
        COMMAND.dialog.bridge.shutdown()
    return True


def register():
    global COMMAND
    from llmlink_c4d.dialog import PLUGIN_ID, LLMLINKCommand
    COMMAND = LLMLINKCommand()
    return c4d.plugins.RegisterCommandPlugin(
        id=PLUGIN_ID, str='LLMLINK', info=0, icon=None,
        help='AI chat for Cinema 4D · Codex and LM Studio', dat=COMMAND)


if __name__ == '__main__':
    try:
        if not register():
            print('LLMLINK: could not register its development command ID')
    except Exception:
        traceback.print_exc()
