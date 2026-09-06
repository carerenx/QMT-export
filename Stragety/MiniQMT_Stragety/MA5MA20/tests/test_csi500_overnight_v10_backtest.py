import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'backtest_csi500_overnight_v10.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('backtest_csi500_overnight_v10', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_simulation_buys_on_v40_bounce_and_forces_next_day_at_ten():
    module = _load_module()
    previous_closes = [80.0] * 15 + [120.0] * 4
    entry_bars = [
        ('09:30:00', 110.0, 110.0, 96.50, 96.60),
    ]
    exit_bars = [
        ('09:30:00', 97.0, 97.1, 96.9, 97.0),
        ('10:00:00', 97.2, 97.3, 97.1, 97.2),
    ]

    trade = module.simulate_candidate_trade(
        'TEST.SZ', '20260901', '20260902', previous_closes,
        entry_bars, exit_bars)

    assert trade['buy_price'] == 96.60
    assert trade['buy_time'] == '09:30:00'
    assert trade['sell_price'] == 97.20
    assert trade['sell_time'] == '10:00:00'
    assert trade['exit_reason'] == 'FORCE_1000'


def test_trade_costs_match_v40_rates():
    module = _load_module()

    costs = module.calculate_trade_costs(100.0, 102.0, 100)

    assert costs['buy_commission'] == 2.5
    assert costs['sell_commission'] == 2.55
    assert costs['stamp_tax'] == 10.2
    assert costs['net_pnl'] == 184.75

