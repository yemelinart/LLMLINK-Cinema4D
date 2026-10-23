"""Cooperative Python limits, not a security sandbox or a native-call watchdog."""
import sys
import time


class BoundedOutput:
    def __init__(self, limit=16000):
        self.limit = limit
        self.value = ''

    def write(self, text):
        self.value = (self.value + text[-self.limit:])[-self.limit:]
        return len(text)

    def flush(self):
        pass

    def getvalue(self):
        return self.value


def execute_python(compiled, namespace, seconds=5):
    """Interrupt ordinary Python loops; a blocking Cinema 4D C operation cannot be interrupted."""
    deadline = time.monotonic() + seconds
    previous = sys.gettrace()
    count = 0

    def trace(frame, event, arg):
        nonlocal count
        if event == 'call':
            frame.f_trace_opcodes = True
        count += 1
        if count % 256 == 0 and time.monotonic() >= deadline:
            raise TimeoutError('Python step exceeded its time limit. Review the scene or restore its checkpoint.')
        return trace

    try:
        sys.settrace(trace)
        exec(compiled, namespace)
    finally:
        sys.settrace(previous)
