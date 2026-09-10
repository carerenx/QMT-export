"""Atomic local checkpoints; deserialization never executes Python objects."""
from collections import deque
from io import StringIO
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd


def _encode(value):
    if isinstance(value, deque):
        return {'__dayt_type__': 'deque', 'data': list(value), 'maxlen': value.maxlen}
    if isinstance(value, set):
        return {'__dayt_type__': 'set', 'data': sorted(value)}
    if isinstance(value, pd.DataFrame):
        return {'__dayt_type__': 'frame', 'data': value.to_json(orient='split', double_precision=15)}
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError('unsupported checkpoint value: ' + type(value).__name__)


def _decode(value):
    kind = value.get('__dayt_type__')
    if kind == 'deque':
        return deque(value['data'], maxlen=value['maxlen'])
    if kind == 'set':
        return set(value['data'])
    if kind == 'frame':
        return pd.read_json(StringIO(value['data']), orient='split')
    return value


def read_checkpoint(path, account):
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'), object_hook=_decode)
    if data.get('schema') != 1 or data.get('account') != str(account):
        raise ValueError('checkpoint schema/account mismatch')
    return data


def write_checkpoint(path, data):
    payload = json.dumps(data, ensure_ascii=False, default=_encode)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
