"""Dockable native Cinema 4D chat, settings, approvals and task queue."""
from pathlib import Path
import time
import uuid
import weakref

import c4d
from .bridge import Bridge
from .context_window import excerpt

ACTIVE_DIALOG = None

PLUGIN_ID = 1069917  # Development ID; obtain a Maxon Plugin Cafe ID before public distribution.
PROVIDER, MODEL, EFFORT, CONNECT = 100, 101, 102, 103
HISTORY, COMPOSER, SEND, STOP, STATUS = 110, 111, 112, 113, 114
ADD, CLEAR_FILES, FILES, MODE, THOUGHTS = 120, 121, 122, 123, 124
APPROVAL_GROUP, APPROVAL_TEXT, CODE, APPLY, DECLINE = 130, 131, 132, 133, 134
QUEUE, RESUME, REMOVE, PROMOTE, CLEAR_QUEUE, QUEUE_STATUS = 140, 141, 142, 143, 144, 145
CHATS, NEW_CHAT, OPEN_CHAT, STEER, GOAL = 150, 151, 152, 153, 154
SETTINGS, SETTINGS_GROUP, ENDPOINT, CODEX_PATH, GOAL_STEPS = 160, 161, 162, 163, 164


class LLMLINKDialog(c4d.gui.GeDialog):
    def __init__(self):
        super().__init__()
        global ACTIVE_DIALOG
        ACTIVE_DIALOG = self
        self.bridge = Bridge()
        reference = weakref.ref(self)
        self.bridge.callback = lambda message: reference() and reference().receive(message)
        self.events = []
        self.attachments = []
        self.models = []
        self.efforts = ['']
        self.chats = []
        self.queue_ids = []
        self.pending = ''
        self.pending_code = ''
        self.pending_submissions = {}
        self.state = {}
        self.config = {'provider': 'codex', 'model': '', 'effort': '', 'endpoint': 'http://127.0.0.1:1234/v1',
                       'codex_path': '', 'apply_mode': 'manual'}
        self.dirty = False
        self.last_draft = None
        self.last_draft_at = 0
        self.last_activity_display = 0
        self.stream = {}
        self.history_text = None

    def CreateLayout(self):
        self.SetTitle('LLMLINK · Cinema 4D')
        h, both = c4d.BFH_SCALEFIT, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT
        self.GroupBegin(10, both, cols=1)
        self.GroupBorderSpace(10, 10, 10, 10)
        self.GroupBegin(11, h, cols=3)
        self.AddStaticText(12, h, name='LLMLINK  /  CINEMA 4D')
        self.AddButton(NEW_CHAT, c4d.BFH_RIGHT, name='New chat')
        self.AddButton(SETTINGS, c4d.BFH_RIGHT, name='Settings')
        self.GroupEnd()
        self.GroupBegin(13, h, cols=3)
        self.AddComboBox(PROVIDER, h, initw=90)
        self.AddChild(PROVIDER, 0, 'Codex')
        self.AddChild(PROVIDER, 1, 'LM Studio')
        self.AddComboBox(MODEL, h, initw=180)
        self.AddButton(CONNECT, c4d.BFH_RIGHT, name='Connect')
        self.GroupEnd()
        self.GroupBegin(14, h, cols=4)
        self.AddStaticText(15, c4d.BFH_LEFT, name='Thinking')
        self.AddComboBox(EFFORT, h, initw=110)
        self.AddComboBox(MODE, h, initw=110)
        self.AddChild(MODE, 0, 'Manual apply')
        self.AddChild(MODE, 1, 'Auto apply')
        self.AddCheckbox(THOUGHTS, c4d.BFH_RIGHT, 0, 0, name='Show thinking')
        self.GroupEnd()
        self.GroupBegin(19, h, cols=2)
        self.AddComboBox(CHATS, h)
        self.AddButton(OPEN_CHAT, c4d.BFH_RIGHT, name='Open chat')
        self.GroupEnd()
        self.AddMultiLineEditText(HISTORY, both, initw=440, inith=260,
                                  style=c4d.DR_MULTILINE_READONLY | c4d.DR_MULTILINE_WORDWRAP)
        self.AddStaticText(APPROVAL_TEXT, h, name='Manual apply · no proposed change')
        self.GroupBegin(APPROVAL_GROUP, h, cols=3)
        self.AddButton(CODE, h, name='View code')
        self.AddButton(APPLY, h, name='Apply change')
        self.AddButton(DECLINE, h, name='Decline')
        self.GroupEnd()
        self.GroupBegin(21, h, cols=3)
        self.AddStaticText(QUEUE_STATUS, h, name='Queue · 0 tasks')
        self.AddButton(RESUME, c4d.BFH_RIGHT, name='Resume')
        self.AddButton(CLEAR_QUEUE, c4d.BFH_RIGHT, name='Clear queue')
        self.GroupEnd()
        self.GroupBegin(22, h, cols=3)
        self.AddComboBox(QUEUE, h, initw=200)
        self.AddButton(PROMOTE, c4d.BFH_RIGHT, name='Run next')
        self.AddButton(REMOVE, c4d.BFH_RIGHT, name='Remove')
        self.GroupEnd()
        self.AddSeparatorH(h)
        self.AddStaticText(FILES, h, name='No attachments')
        self.AddMultiLineEditText(COMPOSER, h, inith=85, style=c4d.DR_MULTILINE_WORDWRAP)
        self.GroupBegin(23, h, cols=4)
        self.AddButton(ADD, c4d.BFH_LEFT, name='+ File')
        self.AddButton(CLEAR_FILES, c4d.BFH_LEFT, name='Clear files')
        self.AddButton(STEER, h, name='Steer')
        self.AddButton(SEND, h, name='Send / Queue')
        self.GroupEnd()
        self.GroupBegin(24, h, cols=2)
        self.AddStaticText(STATUS, h, name='Connecting…')
        self.AddButton(STOP, c4d.BFH_RIGHT, name='Stop')
        self.GroupEnd()
        self.GroupEnd()
        return True

    def InitValues(self):
        self.history_text = None
        self.restore_draft = True
        self.SetInt32(PROVIDER, int(self.config['provider'] == 'lmstudio'))
        self.SetInt32(MODE, int(self.config.get('apply_mode') == 'auto'))
        self.FreeChildren(MODEL)
        if self.models:
            for index, model in enumerate(self.models):
                self.AddChild(MODEL, index, model.get('name', model['id']))
            index = next((i for i, model in enumerate(self.models) if model['id'] == self.config.get('model')), 0)
            self.SetInt32(MODEL, index)
        else:
            self.AddChild(MODEL, 0, 'Connect to list models')
        self.update_efforts()
        self.FreeChildren(QUEUE)
        self.AddChild(QUEUE, 0, 'No queued tasks')
        for ident in (CODE, APPLY, DECLINE):self.Enable(ident, False)
        self.LayoutChanged(10)
        self.SetTimer(100)
        try:
            self.bridge.start()
            if self.bridge.authenticated:
                self.send({'type':'refresh'})
        except Exception as exc:
            self.SetString(STATUS, str(exc))
        self.dirty = True
        return True

    def send(self, message):
        self.bridge.send(message)

    def draft(self):
        return {'text': self.GetString(COMPOSER), 'attachments': list(self.attachments),
                'mode': 'CHAT'}

    def set_files(self):
        self.SetString(FILES, ' · '.join(Path(path).name for path in self.attachments) or 'No attachments')

    def selected_config(self):
        config = dict(self.config)
        config.update(provider='lmstudio' if self.GetInt32(PROVIDER) else 'codex',
                      endpoint=self.config['endpoint'], codex_path=self.config.get('codex_path', ''),
                      apply_mode='auto' if self.GetInt32(MODE) else 'manual')
        index = self.GetInt32(MODEL)
        config['model'] = self.models[index]['id'] if 0 <= index < len(self.models) else ''
        index = self.GetInt32(EFFORT)
        config['effort'] = self.efforts[index] if 0 <= index < len(self.efforts) else ''
        return config

    def update_efforts(self):
        index = self.GetInt32(MODEL)
        model = self.models[index] if 0 <= index < len(self.models) else {}
        self.efforts = [''] + list(dict.fromkeys(e for e in model.get('efforts', []) if e))
        self.FreeChildren(EFFORT)
        for index, value in enumerate(self.efforts):
            self.AddChild(EFFORT, index, value.capitalize() if value else 'Model default')
        selected = self.config.get('effort', '')
        self.SetInt32(EFFORT, self.efforts.index(selected) if selected in self.efforts else 0)
        self.Enable(EFFORT, len(self.efforts) > 1 and not self.state.get('busy'))

    def connect(self):
        if not self.bridge.authenticated:
            self.bridge.start()
            self.SetString(STATUS, 'Connecting…')
            return
        config = self.selected_config()
        self.send({'type': 'settings', 'config': config})
        self.send({'type': 'models', 'config': config})
        self.SetString(STATUS, 'Reading available models…')

    def submit(self, steer=False):
        draft = self.draft()
        if not draft['text'].strip():
            raise ValueError('Write a message first')
        if not self.models:
            raise ValueError('Connect and select a model first')
        ident = uuid.uuid4().hex
        config = self.selected_config()
        message = {'type': 'chat', 'text': draft['text'],
                   'attachments': draft['attachments'], 'client_id': ident, 'steer': steer, 'config':config}
        self.send(message)
        self.pending_submissions[ident] = draft
        self.SetString(COMPOSER, '')
        self.attachments = []
        self.set_files()
        self.Activate(COMPOSER)

    def Command(self, ident, msg):
        try:
            if ident == CONNECT:
                self.connect()
            elif ident == PROVIDER:
                self.models = []
                self.FreeChildren(MODEL)
                self.AddChild(MODEL, 0, 'Connect to list models')
                self.SetInt32(MODEL, 0)
                self.config['model'] = self.config['effort'] = ''
                self.update_efforts()
            elif ident == MODEL:
                self.config['effort'] = ''
                self.update_efforts()
            elif ident == MODE:
                self.send({'type': 'apply_mode', 'mode': 'auto' if self.GetInt32(MODE) else 'manual'})
            elif ident in (SEND, STEER):
                self.submit(ident == STEER)
            elif ident == STOP:
                self.send({'type': 'stop'})
            elif ident in (APPLY, DECLINE):
                self.send({'type': 'approve', 'id': self.pending, 'allow': ident == APPLY})
                self.Enable(APPLY, False)
                self.Enable(DECLINE, False)
            elif ident == ADD:
                if len(self.attachments) >= 6:
                    raise ValueError('Attach up to six files per message')
                path = c4d.storage.LoadDialog(title='Attach image or file', flags=c4d.FILESELECT_LOAD)
                if path and path not in self.attachments:
                    self.attachments.append(path)
                self.set_files()
            elif ident == CLEAR_FILES:
                self.attachments = []
                self.set_files()
            elif ident == RESUME:
                self.send({'type': 'queue_resume'})
            elif ident == CLEAR_QUEUE:
                self.send({'type': 'queue_clear'})
            elif ident in (REMOVE, PROMOTE):
                index = self.GetInt32(QUEUE)
                if 0 <= index < len(self.queue_ids):
                    self.send({'type': 'queue_remove' if ident == REMOVE else 'queue_now', 'id': self.queue_ids[index]})
            elif ident == NEW_CHAT:
                self.send({'type': 'new_chat', 'draft': self.draft()})
            elif ident == OPEN_CHAT:
                index = self.GetInt32(CHATS)
                if 0 <= index < len(self.chats):
                    self.send({'type': 'switch_chat', 'id': self.chats[index]['id'], 'draft': self.draft()})
            elif ident == SETTINGS:
                settings = SettingsDialog(self.config)
                settings.Open(c4d.DLG_TYPE_MODAL, defaultw=480, defaulth=150)
                if settings.value:
                    self.config.update(settings.value)
                    self.connect()
            elif ident == CODE:
                code_dialog = CodeDialog(self.pending_code)
                code_dialog.Open(c4d.DLG_TYPE_MODAL, defaultw=650, defaulth=420)
            elif ident == THOUGHTS:
                self.dirty = True
        except Exception as exc:
            self.SetString(STATUS, str(exc))
        return True

    def receive(self, message):
        kind = message.get('type')
        if kind == 'connected':
            self.SetString(STATUS, 'Ready · Connect to list models')
        elif kind == 'bridge_error':
            self.recovering_submissions = True
            self.state = {**self.state, 'busy': False}
            self.pending = ''
            self.pending_code = ''
            for ident in (CODE, APPLY, DECLINE, RESUME, REMOVE, PROMOTE, CLEAR_QUEUE):
                self.Enable(ident, False)
            self.Enable(CONNECT, True)
            self.Enable(SETTINGS, True)
            self.SetString(STATUS, message['error'])
        elif kind == 'chat_opened':
            self.events = []
            self.stream = {}
            draft = message.get('draft', {})
            if getattr(self,'restore_draft',False) or self.state.get('chat_id') != message.get('id'):
                self.SetString(COMPOSER, draft.get('text', ''))
                self.attachments = draft.get('attachments', [])
                self.set_files()
            self.restore_draft = False
            self.last_draft = None
            self.dirty = True
        elif kind == 'event':
            event = message['event']
            event_kind = event.get('kind')
            if event_kind == 'stream':
                channel = event.get('channel', 'working')
                self.stream[channel] = (self.stream.get(channel, '') + event.get('text', ''))[-16000:]
            elif event_kind == 'step_start':
                self.stream = {}
            elif event_kind == 'step_end':
                # Summarized thoughts arrive in saved history after reopening.
                if self.stream.get('reasoning'):
                    self.events.append({'kind': 'thought', 'text': self.stream['reasoning']})
                self.stream = {}
            else:
                self.events.append(event)
                self.events = self.events[-1500:]
            if event_kind in ('status', 'error'):
                self.SetString(STATUS, event.get('text', ''))
            client_id = event.get('client_id', '')
            if client_id and client_id in self.pending_submissions:
                draft = self.pending_submissions.pop(client_id)
                if event_kind == 'error':
                    if not self.GetString(COMPOSER).strip() and not self.attachments:
                        self.SetString(COMPOSER, draft['text'])
                        self.attachments = draft['attachments']
                        self.set_files()
                    else:
                        self.events.append({'kind':'info', 'text':'Message was not sent; your current draft is preserved. Unsent message:\n'+draft['text'],
                                            'attachments':[{'name':str(path)} for path in draft['attachments']]})
            self.dirty = True
        elif kind == 'state':
            # Replayed acknowledgements remove accepted submissions first.
            # Any remaining submission after reconnect has an unknown outcome;
            # keep its text visible without automatically sending it twice.
            if getattr(self,'recovering_submissions',False) and message.get('chat_id'):
                accepted = {e.get('client_id') for e in self.events if e.get('kind')=='user'}
                for ident, draft in list(self.pending_submissions.items()):
                    if ident not in accepted:
                        self.events.append({'kind':'info','text':'Delivery has not been confirmed. Review the chat before resending:\n'+draft['text']})
                        self.dirty=True
                    self.pending_submissions.pop(ident,None)
                self.recovering_submissions=False
            self.state = message
            self.config.update(message.get('config', {}))
            self.SetInt32(PROVIDER, int(self.config['provider'] == 'lmstudio'))
            self.SetInt32(MODE, int(self.config.get('apply_mode') == 'auto'))
            busy = bool(message.get('busy'))
            for ident in (PROVIDER, MODEL, CONNECT, SETTINGS):
                self.Enable(ident, not busy)
            self.Enable(EFFORT, not busy and len(self.efforts) > 1)
            self.queue_ids = message.get('queue_ids', [])
            self.FreeChildren(QUEUE)
            users = {e.get('queue_id'): e.get('text', '') for e in self.events if e.get('kind') == 'user'}
            for index, queue_id in enumerate(self.queue_ids):
                self.AddChild(QUEUE, index, '%d. %s' % (index+1, users.get(queue_id, 'Task')[:64].replace('\n', ' ')))
            if not self.queue_ids:
                self.AddChild(QUEUE, 0, 'No queued tasks')
            self.SetInt32(QUEUE, 0)
            self.SetString(QUEUE_STATUS, 'Queue · %d tasks%s' % (len(self.queue_ids), ' · paused' if message.get('queue_paused') else ''))
            for ident in (REMOVE, PROMOTE, CLEAR_QUEUE):
                self.Enable(ident, bool(self.queue_ids))
            self.Enable(RESUME, bool(self.queue_ids) and message.get('queue_paused', False))
        elif kind == 'approval':
            self.pending = message.get('id', '')
            self.pending_code = message.get('code', '')
            self.SetString(APPROVAL_TEXT, message.get('message', '')[:100] if self.pending else 'No proposed change')
            for ident in (CODE, APPLY, DECLINE):self.Enable(ident, bool(self.pending))
        elif kind == 'models':
            provider = 'lmstudio' if self.GetInt32(PROVIDER) else 'codex'
            if message.get('provider') != provider:
                return
            self.models = message.get('models', [])
            self.FreeChildren(MODEL)
            for index, model in enumerate(self.models):
                self.AddChild(MODEL, index, model.get('name', model['id']))
            selected = next((i for i, model in enumerate(self.models) if model['id'] == self.config.get('model')), 0)
            self.SetInt32(MODEL, selected)
            self.update_efforts()
            self.SetString(STATUS, message.get('error') or ('Connected · %d models' % len(self.models)))
            if self.models:
                self.send({'type': 'settings', 'config': self.selected_config()})
        elif kind == 'chats':
            self.chats = message.get('chats', [])
            self.FreeChildren(CHATS)
            for index, chat in enumerate(self.chats):
                self.AddChild(CHATS, index, chat['title'])
            active = next((i for i, chat in enumerate(self.chats) if chat['id'] == message.get('active')), 0)
            self.SetInt32(CHATS, active)

    def redraw_history(self):
        blocks = []
        show_thoughts = self.GetBool(THOUGHTS)
        for event in self.events:
            kind = event.get('kind')
            if kind in ('user', 'assistant', 'error', 'info'):
                label = {'user': 'YOU', 'assistant': 'LLMLINK', 'error': 'ERROR', 'info': 'INFO'}[kind]
                text = label + '\n' + event.get('text', '')
                if event.get('attachments'):
                    text += '\n' + ' · '.join('File: ' + ref.get('name', '') for ref in event['attachments'])
                blocks.append(text)
            elif kind == 'tool':
                result = event.get('result', {})
                if result.get('output'):
                    blocks.append('RESULT\n' + excerpt(result['output'], 3000))
                if result.get('error'):
                    blocks.append('STEP ERROR\n' + result['error'])
            elif kind == 'thought' and show_thoughts:
                blocks.append('THINKING\n' + event.get('text', ''))
        if show_thoughts and self.stream.get('reasoning'):
            blocks.append('THINKING\n' + self.stream['reasoning'])
        text = '\n\n'.join(blocks)[-180000:]
        displayed = text or 'Describe what you want to create or change in the current scene.\n\nConnect to Codex or LM Studio, select a model, then send a task.\nAdditional messages wait in the queue. Manual apply lets you review each change.'
        if displayed != getattr(self, 'history_text', None):
            # SetMultiLinePos also focuses the target control in Cinema 4D 2024.
            # Updating text alone preserves the composer's caret and selection.
            composing = self.IsActive(COMPOSER)
            self.SetString(HISTORY, displayed)
            if text and not composing and self.IsActive(HISTORY):
                self.SetMultiLinePos(HISTORY, text.count('\n'), 0)
            self.history_text = displayed
        self.dirty = False

    def Timer(self, msg):
        self.bridge.tick()
        if self.dirty:
            self.redraw_history()
        if self.state.get('busy') and time.monotonic() - self.last_activity_display > 1:
            activity = self.state.get('activity', {})
            label = {'waiting':'Waiting for model', 'thinking':'Thinking', 'responding':'Responding',
                     'inspecting':'Reading scene', 'executing':'Applying change', 'approval':'Waiting for your approval',
                     'repairing':'Repairing step', 'stopping':'Stopping', 'uncertain':'Waiting for Cinema 4D'}.get(activity.get('state'), 'Working')
            if self.bridge.executor.render:
                label = 'Rendering'
            elapsed = max(0, int(time.time() - (activity.get('task_started') or time.time())))
            self.SetString(STATUS, '%s · %d:%02d' % (label, elapsed // 60, elapsed % 60))
            self.last_activity_display = time.monotonic()
        if self.bridge.authenticated and time.monotonic() - self.last_draft_at > 1:
            draft = self.draft()
            if draft != self.last_draft and not self.pending_submissions:
                try:
                    self.send({'type': 'draft', 'draft': draft})
                    self.last_draft = draft
                except Exception as exc:
                    self.SetString(STATUS, str(exc))
            self.last_draft_at = time.monotonic()

    def AskClose(self):
        if self.bridge.executor.render and self.bridge.executor.render.IsRunning():
            self.bridge.executor.cancel()
            self.SetString(STATUS, 'Stopping render… close again once it finishes.')
            return True
        if self.bridge.authenticated:
            try:
                self.send({'type': 'draft', 'draft': self.draft()})
                self.bridge.tick()
            except Exception:
                pass
        self.bridge.close()
        return False

    def DestroyWindow(self):
        # Also invoked during docking/layout changes. Timer resumes on InitValues.
        self.SetTimer(0)


class SettingsDialog(c4d.gui.GeDialog):
    def __init__(self, config):
        super().__init__()
        self.config = dict(config)
        self.value = None
    def CreateLayout(self):
        self.SetTitle('LLMLINK · Settings')
        self.GroupBegin(500, c4d.BFH_SCALEFIT, cols=2)
        self.GroupBorderSpace(12,12,12,12)
        self.AddStaticText(501, c4d.BFH_LEFT, name='LM Studio URL')
        self.AddEditText(ENDPOINT, c4d.BFH_SCALEFIT, initw=300)
        self.AddStaticText(502, c4d.BFH_LEFT, name='Codex executable')
        self.AddEditText(CODEX_PATH, c4d.BFH_SCALEFIT, initw=300)
        self.GroupEnd()
        self.AddDlgGroup(c4d.DLG_OK | c4d.DLG_CANCEL)
        return True
    def InitValues(self):
        self.SetString(ENDPOINT, self.config['endpoint'])
        self.SetString(CODEX_PATH, self.config.get('codex_path', ''))
        return True
    def Command(self, ident, msg):
        if ident == c4d.IDC_OK:
            self.value = {'endpoint':self.GetString(ENDPOINT).strip(),'codex_path':self.GetString(CODEX_PATH).strip()}
            self.Close()
        elif ident == c4d.IDC_CANCEL:self.Close()
        return True

class CodeDialog(c4d.gui.GeDialog):
    def __init__(self, code):
        super().__init__()
        self.code = code
    def CreateLayout(self):
        self.SetTitle('LLMLINK · Proposed Python')
        self.AddMultiLineEditText(600, c4d.BFH_SCALEFIT|c4d.BFV_SCALEFIT, initw=600, inith=350,
                                 style=c4d.DR_MULTILINE_READONLY|c4d.DR_MULTILINE_MONOSPACED)
        self.AddDlgGroup(c4d.DLG_OK)
        return True
    def InitValues(self):
        self.SetString(600,self.code)
        return True
    def Command(self, ident, msg):
        if ident == c4d.IDC_OK:self.Close()
        return True


class LLMLINKCommand(c4d.plugins.CommandData):
    def __init__(self):
        self.dialog = LLMLINKDialog()

    def Execute(self, doc):
        return self.dialog.Open(c4d.DLG_TYPE_ASYNC, PLUGIN_ID, defaultw=520, defaulth=780)

    def RestoreLayout(self, secret):
        return self.dialog.Restore(PLUGIN_ID, secret)
