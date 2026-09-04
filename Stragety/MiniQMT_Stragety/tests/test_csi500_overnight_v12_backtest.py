import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'backtest_csi500_overnight_v12.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('backtest_csi500_overnight_v12', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_backtest_holds_beyond_old_stop_and_forces_at_ten():
    module = _load_module()
    bars = [
        ('09:30:00', 98.0, 98.2, 97.8, 98.0),
        ('10:00:00', 99.0, 99.1, 98.9, 99.0),
    ]

    trade = module._exit_without_stop(
        'TEST.SZ', '20260901', '20260902', '14:00:00', 100.0, bars)

    assert trade['exit_reason'] == 'FORCE_1000'
    assert trade['sell_time'] == '10:00:00'
    assert trade['sell_price'] == 98.9505
