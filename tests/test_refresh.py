import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin' / 'LLMLINK' / 'llmlink_c4d'))
from companion import Session
from protocol import encode


class RefreshTests(unittest.TestCase):
    def test_panel_recreation_replays_pending_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            left, right = socket.socketpair()
            session = Session(left, 'x'*40, folder)
            sent = []
            session.send = sent.append
            session.native_ready = True
            session.pending = {'id':'pending-step', 'message':'Create cube', 'code':'print(1)', 'decision':None}
            reader = threading.Thread(target=session.bridge_reader)
            reader.start()
            try:
                right.sendall(encode({'type':'refresh'}))
                deadline = time.monotonic()+3
                while not any(m.get('type')=='approval' for m in sent) and time.monotonic()<deadline:
                    time.sleep(.01)
                approval = next(m for m in sent if m.get('type')=='approval')
                self.assertEqual(approval['id'], 'pending-step')
                self.assertEqual(approval['code'], 'print(1)')
                self.assertTrue(any(m.get('type')=='chat_opened' for m in sent))
                self.assertTrue(any(m.get('type')=='state' for m in sent))
            finally:
                right.shutdown(socket.SHUT_RDWR)
                right.close()
                reader.join(timeout=3)
                left.close()
                self.assertFalse(reader.is_alive())
