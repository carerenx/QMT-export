"""Atomic JSON persistence for one trading session."""
import json
import os


class AtomicJsonStateStore:
    def __init__(self, path, json_default=None):
        self.path = os.path.abspath(path)
        self.json_default = json_default

    def load_for_date(self, trade_date):
        value = self.load_latest()
        if not isinstance(value, dict) or value.get('trade_date') != trade_date:
            return None
        return value

    def load_latest(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None

    def save(self, state):
        state_dir = os.path.dirname(self.path)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        temporary_path = self.path + '.tmp'
        with open(temporary_path, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2,
                      default=self.json_default)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, self.path)


class InMemoryStateStore:
    """State-store Adapter for deterministic tests and non-restoring runs."""

    def __init__(self):
        self.value = None

    def load_for_date(self, trade_date):
        if self.value and self.value.get('trade_date') == trade_date:
            return dict(self.value)
        return None

    def load_latest(self):
        return dict(self.value) if self.value else None

    def save(self, state):
        self.value = dict(state)
