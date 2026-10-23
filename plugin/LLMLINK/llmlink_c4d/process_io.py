"""Bounded subprocess output; backpressure instead of unlimited event queues."""
from collections import deque
import json
import queue
import threading

MAX_LINE = 2 * 1024 * 1024


def read_process(proc, cancelled, parse_json=True):
    events = queue.Queue(maxsize=32)
    errors = deque(maxlen=40)
    done = threading.Event()
    proc.llmlink_reader_done = done

    def put(value):
        while not done.is_set() and not cancelled.is_set():
            try:
                events.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def readout():
        try:
            while not done.is_set() and not cancelled.is_set():
                line = proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise RuntimeError('Provider output line exceeded its limit')
                if parse_json:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        raise RuntimeError('Provider event must be an object')
                else:
                    event = line
                put(event)
        except (OSError, ValueError, RuntimeError) as exc:
            put(exc)
        finally:
            put(None)

    def readerr():
        try:
            while not done.is_set():
                text = proc.stderr.readline(4096)
                if not text:
                    break
                errors.append(text)
        except (OSError, ValueError):
            pass

    for reader in (readout, readerr):
        threading.Thread(target=reader, daemon=True).start()
    return events, errors
