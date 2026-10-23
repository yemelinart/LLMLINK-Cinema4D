"""Authenticated transport to an isolated Python companion; no C4D calls in workers."""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

import c4d
from .protocol import VERSION, encode, decode, MAX_MESSAGE
from .scene_tools import SceneExecutor


def python_executable(root):
    config = root / 'runtime.json'
    if config.is_file():
        configured = json.loads(config.read_text()).get('python', '')
        if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
    for path in (shutil.which('python3'), '/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3'):
        if path and Path(path).is_file() and os.access(path, os.X_OK) and 'Cinema' not in path:
            return path
    raise RuntimeError('Python 3.9 or newer was not found. Run the LLMLINK installer.')


class Bridge:
    def __init__(self):
        self.root = Path(__file__).resolve().parent.parent
        self.data = Path(c4d.storage.GeGetC4DPath(c4d.C4D_PATH_PREFS)) / 'LLMLINK'
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.executor = SceneExecutor(self.data)
        self.server = self.socket = self.process = self.log = None
        self.buffer, self.outgoing = bytearray(), bytearray()
        self.authenticated = False
        self.deadline = 0
        self.token = ''
        self.callback = lambda msg: None
        self.failed = False

    def start(self):
        if self.process and self.process.poll() is None and not self.failed:
            return
        if self.executor.render:
            if self.executor.render.IsRunning():
                raise RuntimeError('Waiting for the previous render to stop. Reconnect once it finishes.')
            self.executor.poll_render()  # Do not deliver an old result to a new session.
        self.close()
        self.failed = False
        self.token = secrets.token_urlsafe(40)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(4)
        self.server.setblocking(False)
        self.deadline = time.monotonic() + 25
        self.log = (self.data / 'companion.log').open('w', encoding='utf-8')
        os.chmod(self.data / 'companion.log', 0o600)
        try:
            env = dict(os.environ)
            # Cinema's embedded interpreter variables must not poison external Python.
            for key in ('PYTHONHOME', 'PYTHONPATH'):
                env.pop(key, None)
            self.process = subprocess.Popen(
                [python_executable(self.root), '-u', str(self.root / 'llmlink_c4d' / 'companion.py'),
                 '--port', str(self.server.getsockname()[1]), '--data', str(self.data)],
                stdin=subprocess.PIPE, stdout=self.log, stderr=self.log, env=env,
                cwd=str(self.data), start_new_session=True)
            self.process.stdin.write((self.token + '\n').encode())
            self.process.stdin.close()
        except Exception:
            self.close()
            raise

    def send(self, message):
        if not self.authenticated:
            raise RuntimeError('LLMLINK is connecting. Try again when Ready appears.')
        packet = encode(message)
        if len(self.outgoing) + len(packet) > 8 * MAX_MESSAGE:
            raise RuntimeError('Connection is busy; wait for Cinema 4D to acknowledge it')
        self.outgoing.extend(packet)

    def tick(self):
        if self.failed or self.process is None:
            return
        try:
            result = self.executor.poll_render()
            if result:
                self.send(result)
            if not self.authenticated and time.monotonic() > self.deadline:
                raise RuntimeError('Companion did not connect. See LLMLINK/companion.log in Cinema preferences.')
            if self.process.poll() is not None:
                raise RuntimeError('LLMLINK companion stopped. Reconnect; chat and queue are saved.')
            if self.socket is None:
                try:
                    self.socket, _ = self.server.accept()
                    self.socket.setblocking(False)
                except BlockingIOError:
                    return
            if self.outgoing:
                try:
                    sent = self.socket.send(self.outgoing[:65536])
                    del self.outgoing[:sent]
                except BlockingIOError:
                    pass
            for _ in range(8):
                try:
                    chunk = self.socket.recv(65536)
                except BlockingIOError:
                    break
                if not chunk:
                    raise RuntimeError('Companion connection closed. Reconnect to restore the chat.')
                self.buffer.extend(chunk)
                if len(self.buffer) > 8 * MAX_MESSAGE:
                    raise ValueError('Companion input exceeded its limit')
            # A few messages per tick keep the interface responsive while streaming.
            for msg in decode(self.buffer, limit=24):
                if not self.authenticated:
                    if msg.get('type') != 'hello' or msg.get('version') != VERSION or not secrets.compare_digest(msg.get('token', ''), self.token):
                        raise RuntimeError('Companion authentication failed')
                    self.authenticated = True
                    self.server.close()
                    self.server = None
                    self.callback({'type': 'connected'})
                elif msg.get('type') == 'command':
                    result = self.executor.execute(msg)
                    if result:
                        self.send(result)
                elif msg.get('type') == 'cancel':
                    self.executor.cancel()
                else:
                    self.callback(msg)
        except Exception as exc:
            self.failed = True
            self.authenticated = False
            self.executor.cancel()
            if self.socket:
                self.socket.close()
                self.socket = None
            self.callback({'type': 'bridge_error', 'error': str(exc)})

    def close(self):
        self.executor.cancel()
        for sock in (self.socket, self.server):
            if sock:
                sock.close()
        self.socket = self.server = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            self.process = None
        if self.log:
            self.log.close()
            self.log = None
        self.authenticated = False
        self.buffer.clear()
        self.outgoing.clear()

    def shutdown(self):
        self.executor.shutdown()
        self.close()
