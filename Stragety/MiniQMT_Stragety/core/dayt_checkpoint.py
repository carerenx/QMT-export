"""Atomic local checkpoints; deserialization never executes Python objects."""
from collections import deque
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np
import pandas as pd

# 仅重试Windows替换文件的访问/共享/锁冲突，合计等待3秒，不重发交易委托。
REPLACE_RETRY_DELAYS = (0.2, 0.4, 0.8, 1.6)


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


def write_checkpoint(path, data, log=None):
    payload = json.dumps(
        data, ensure_ascii=False, default=_encode, indent=2) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(len(REPLACE_RETRY_DELAYS) + 1):
            try:
                os.replace(temporary, path)
                if attempt and log:
                    log('[STATE-SAVE-RECOVERED] replacement succeeded on attempt {}'.format(attempt + 1))
                break
            except OSError as error:
                if (getattr(error, 'winerror', None) not in (5, 32, 33) or
                        attempt >= len(REPLACE_RETRY_DELAYS)):
                    raise
                delay = REPLACE_RETRY_DELAYS[attempt]
                if log:
                    log('[STATE-SAVE-RETRY] winerror={} attempt={}/{} wait={:.1f}s path={}'.format(
                        error.winerror, attempt + 1, len(REPLACE_RETRY_DELAYS) + 1, delay, path))
                time.sleep(delay)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError as error:
                # Preserve the original replacement failure, not a cleanup error.
                if log:
                    log('[STATE-TEMP-CLEANUP] retained {}: {}'.format(temporary, error))
