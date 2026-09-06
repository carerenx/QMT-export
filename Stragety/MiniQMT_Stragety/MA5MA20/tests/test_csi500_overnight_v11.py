import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v11_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v11', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_market_filter_requires_positive_index_trend_and_limited_drop():
    module = _load_module()
    rising = [100.0 + index for index in range(25)]
    falling = list(reversed(rising))

    assert module.is_market_allowed(rising, 125.0) is True
    assert module.is_market_allowed(falling, 100.0) is False
    assert module.is_market_allowed(rising, rising[-1] * 0.98) is False


def test_buy_requires_three_minutes_and_point_three_percent_bounce():
    module = _load_module()
    state = module.new_buy_watch(100.0, 97.0, '09:30:00')

    assert module.advance_buy_watch(state, 97.0, '09:30:00') is None
    assert state['phase'] == 'DIPPING'
    assert module.advance_buy_watch(state, 96.5, '09:31:00') is None
    assert module.advance_buy_watch(state, 96.9, '09:33:00') is None
    assert module.advance_buy_watch(state, 96.9, '09:34:00') == 'BUY'


def test_new_low_resets_stability_timer():
    module = _load_module()
    state = module.new_buy_watch(100.0, 97.0, '09:30:00')
    module.advance_buy_watch(state, 97.0, '09:30:00')
    module.advance_buy_watch(state, 96.5, '09:32:00')

    assert module.advance_buy_watch(state, 96.9, '09:34:00') is None
    assert module.advance_buy_watch(state, 96.9, '09:35:00') == 'BUY'

