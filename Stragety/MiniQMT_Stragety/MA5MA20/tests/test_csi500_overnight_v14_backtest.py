import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'backtest_csi500_overnight_v14.py'


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'backtest_csi500_overnight_v14', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_delayed_trade_records_separate_screen_buy_and_sell_dates():
    module = _load_module()
    entry_bars = [
        ('09:30:00', 100.0, 100.0, 100.0, 100.0),
        ('09:31:00', 100.0, 100.0, 97.0, 97.0),
        ('09:34:00', 97.0, 97.4, 97.0, 97.4),
    ]
    exit_bars = [
        ('09:30:00', 98.0, 98.2, 97.8, 98.0),
        ('10:00:00', 99.0, 99.1, 98.9, 99.0),
    ]
    index_bars = [
        (bar[0], 101.0, 101.0, 101.0, 101.0) for bar in entry_bars]
    index_closes = [float(value) for value in range(76, 101)]

    trade = module.simulate_delayed_trade(
        'TEST.SZ', '20260901', '20260902', '20260903', entry_bars,
        exit_bars, index_bars, index_closes, set())

    assert trade['screen_date'] == '20260901'
    assert trade['buy_date'] == '20260902'
    assert trade['sell_date'] == '20260903'
    assert trade['exit_reason'] == 'FORCE_1000'
