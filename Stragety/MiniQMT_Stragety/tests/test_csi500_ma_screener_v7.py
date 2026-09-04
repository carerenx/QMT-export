import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v7_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v7', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_ma5_below_percentage_is_configurable():
    module = _load_module()
    completed = [80.0] * 15 + [120.0] * 4

    assert module.calculate_ma_candidate(
        completed, 105.0, below_ma5_percent=4.0)['matched'] is True
    assert module.calculate_ma_candidate(
        completed, 105.0, below_ma5_percent=11.0)['matched'] is False


def test_default_percentage_is_a_valid_percent():
    module = _load_module()

    assert isinstance(module.MA5_BELOW_PERCENT, (int, float))
    assert 0 <= module.MA5_BELOW_PERCENT < 100
