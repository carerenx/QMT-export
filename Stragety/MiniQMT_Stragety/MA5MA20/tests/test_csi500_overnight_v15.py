import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v15_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v15', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _rows(closes):
    return [(value + 1.0, value - 1.0, float(value)) for value in closes]


def test_optimized_candidate_uses_completed_ma_and_atr_pullback():
    module = _load_module()
    result = module.calculate_optimized_candidate(
        _rows(range(2, 27)), 22.9, variant='atr_normalized')

    assert result['ma5'] == 24.0
    assert result['ma20'] == 16.5
    assert result['atr14'] == 2.0
    assert abs(result['pullback_atr'] - 0.55) < 1e-12
    assert result['matched'] is True


def test_pullback_below_half_atr_is_rejected():
    module = _load_module()

    assert module.calculate_optimized_candidate(
        _rows(range(2, 27)), 23.2,
        variant='atr_normalized')['matched'] is False


def test_falling_ma20_is_rejected_even_when_price_is_between_mas():
    module = _load_module()
    closes = [200.0] * 5 + [100.0] * 15 + [120.0] * 5
    result = module.calculate_optimized_candidate(_rows(closes), 110.0)

    assert result['ma5'] > result['price'] > result['ma20']
    assert result['ma20_rising'] is False
    assert result['matched'] is False


def test_default_normalized_pullback_is_half_atr():
    module = _load_module()

    assert module.MIN_PULLBACK_ATR == 0.5


def test_final_rule_requires_seven_percent_atr_and_three_percent_pullback():
    module = _load_module()
    high_atr = module.calculate_optimized_candidate(
        _rows(range(2, 27)), 22.9)
    low_atr_rows = [
        (value + 0.1, value - 0.1, float(value)) for value in range(102, 127)]
    low_atr = module.calculate_optimized_candidate(low_atr_rows, 118.0)

    assert high_atr['matched'] is True
    assert low_atr['gap_pct'] < -3.0
    assert low_atr['atr_percent'] < 7.0
    assert low_atr['matched'] is False
