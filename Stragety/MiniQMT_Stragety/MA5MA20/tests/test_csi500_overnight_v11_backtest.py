import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'backtest_csi500_overnight_v11.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('backtest_csi500_overnight_v11', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_minimum_commission_and_slippage_are_applied():
    module = _load_module()
    costs = module.calculate_trade_costs(10.0, 10.2, 100)

    assert costs['buy_commission'] == 5.0
    assert costs['sell_commission'] == 5.0
    assert costs['stamp_tax'] == 1.02
    assert costs['net_pnl'] == 8.98
    assert module._slipped(100.0, 'BUY') == 100.05
    assert module._slipped(100.0, 'SELL') == 99.95


def test_continuous_threshold_fill_keeps_opening_gap_price():
    module = _load_module()

    assert module._continuous_fill(100.0, 98.0, 98.5, 'DOWN') == 98.5
    assert module._continuous_fill(None, 96.0, 98.5, 'DOWN') == 96.0


def test_simulation_uses_stability_entry_and_next_day_force_exit():
    module = _load_module()
    previous = [80.0] * 15 + [120.0] * 4
    entry = [
        ('09:30:00', 110.0, 110.0, 104.0, 105.0),
        ('09:31:00', 105.0, 105.0, 96.5, 96.5),
        ('09:32:00', 96.5, 96.6, 96.5, 96.6),
        ('09:33:00', 96.6, 96.7, 96.5, 96.7),
        ('09:34:00', 96.7, 96.9, 96.6, 96.8),
    ]
    exit_bars = [
        ('09:30:00', 97.0, 97.1, 96.9, 97.0),
        ('10:00:00', 97.2, 97.3, 97.1, 97.2),
    ]
    index_bars = [(row[0], 125.0, 125.0, 125.0, 125.0) for row in entry]
    trade = module.simulate_candidate_trade(
        'TEST.SZ', '20260901', '20260902', previous, entry, exit_bars,
        index_bars, [100.0 + index for index in range(25)], set())

    assert trade['buy_time'] == '09:34:00'
    assert trade['exit_reason'] == 'FORCE_1000'
    assert trade['sell_time'] == '10:00:00'
