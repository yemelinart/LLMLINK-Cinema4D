"""Versioned, bounded messages shared by the add-on and its companion."""
import json
import struct
try:
    from .code_policy import validate_code
except ImportError:
    from code_policy import validate_code

VERSION = 1
MAX_MESSAGE = 2 * 1024 * 1024


def encode(message):
    body = json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(body) > MAX_MESSAGE:
        raise ValueError('Message exceeds 2 MiB')
    return struct.pack('!I', len(body)) + body


def decode(buffer, limit=None):
    messages = []
    while len(buffer) >= 4 and (limit is None or len(messages) < limit):
        length = struct.unpack('!I', buffer[:4])[0]
        if length > MAX_MESSAGE:
            raise ValueError('Message exceeds 2 MiB')
        if len(buffer) < length + 4:
            break
        message = json.loads(bytes(buffer[4:4 + length]))
        if not isinstance(message, dict):
            raise ValueError('Protocol message must be an object')
        messages.append(message)
        del buffer[:4 + length]
    return messages


STEP_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': ['inspect', 'python', 'done', 'recall']},
        'message': {'type': 'string'},
        'code': {'type': 'string'},
    }, 'required': ['action', 'message', 'code'],
}


def validate_step(step):
    if not isinstance(step, dict) or set(step) != {'action', 'message', 'code'}:
        raise ValueError('Invalid model response: expected action, message, code')
    if step['action'] not in ('inspect', 'python', 'done', 'recall'):
        raise ValueError('Unknown action')
    if not all(isinstance(step[k], str) for k in ('action', 'message', 'code')):
        raise ValueError('Invalid field types')
    if len(step['code']) > 50000 or len(step['message']) > 16000:
        raise ValueError('Model response too large')
    if step['action'] == 'python':
        if not step['code'].strip():
            raise ValueError('Empty Python command')
        validate_code(step['code'])
    elif step['code']:
        raise ValueError('Only Python actions may contain code')
    return step
