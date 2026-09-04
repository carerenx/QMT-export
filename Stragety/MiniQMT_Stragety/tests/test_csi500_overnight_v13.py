import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v13_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v13', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_atr_percent_uses_previous_close_and_fourteen_complete_days():
    module = _load_module()
    rows = [(107.0, 93.0, 100.0)] * 15

    assert abs(module.calculate_atr_percent(rows) - 14.0) < 1e-12


def test_atr_percent_rejects_insufficient_history():
    module = _load_module()

    assert module.calculate_atr_percent([(107.0, 93.0, 100.0)] * 14) is None


def test_default_atr_threshold_is_seven_percent():
    module = _load_module()

    assert module.ATR_PERIOD == 14
    assert module.MIN_ATR_PERCENT == 7.0


def test_date_key_accepts_qmt_millisecond_timestamp():
    module = _load_module()

    assert module._date_key(1788393600000) == '20260903'
