import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v1_miniqmt.py'
SPEC = importlib.util.spec_from_file_location('csi500_ma_screener_v1', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Column(list):
    def tolist(self):
        return list(self)


class _Frame:
    def __init__(self, dates, closes):
        self.columns = ['time', 'close']
        self._values = {
            'time': _Column(dates),
            'close': _Column(closes),
        }

    def __len__(self):
        return len(self._values['close'])

    def __getitem__(self, key):
        return self._values[key]


def test_calculate_ma_candidate_uses_live_price_and_matches():
    completed = [80.0] * 15 + [120.0] * 4

    result = MODULE.calculate_ma_candidate(completed, 105.0)

    assert result['ma5'] == 117.0
    assert result['ma20'] == 89.25
    assert result['matched'] is True


def test_calculate_ma_candidate_requires_19_completed_closes():
    assert MODULE.calculate_ma_candidate([100.0] * 18, 95.0) is None


def test_extract_completed_closes_excludes_current_day_and_sorts():
    frame = _Frame(
        [20260902000000, 20260901000000, 20260903000000],
        [102.0, 101.0, 103.0],
    )

    closes = MODULE.extract_completed_closes(frame, '20260903')

    assert closes == [101.0, 102.0]
