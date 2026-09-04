import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v10_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v10', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_screen_rule_is_the_configured_ma5_gap_and_above_ma20():
    module = _load_module()
    completed = [80.0] * 15 + [120.0] * 4

    assert module.is_screen_match(completed, 105.0) is True
    assert module.is_screen_match(completed, 80.0) is False


def test_v40_buy_waits_for_dip_then_point_one_percent_bounce():
    module = _load_module()
    state = module.new_buy_watch(open_price=100.0, current_price=97.0)

    assert module.advance_buy_watch(state, 97.0) is None
    assert state['phase'] == 'DIPPING'
    assert module.advance_buy_watch(state, 96.50) is None
    assert module.advance_buy_watch(state, 96.60) == 'BUY'


def test_sellback_only_starts_next_day_and_uses_target_then_pullback():
    module = _load_module()
    state = module.new_sell_watch('20260901', 100.0)

    assert module.advance_sell_watch(
        state, '20260901', '14:30:00', 102.0) is None
    assert state['phase'] == 'BOUGHT'
    assert module.advance_sell_watch(
        state, '20260902', '09:35:00', 101.20) is None
    assert state['phase'] == 'SPIKING'
    assert module.advance_sell_watch(
        state, '20260902', '09:36:00', 101.30) is None
    assert module.advance_sell_watch(
        state, '20260902', '09:37:00', 101.19) == 'SELLBACK'


def test_next_day_stop_loss_precedes_ten_and_ten_forces_exit():
    module = _load_module()
    stop_state = module.new_sell_watch('20260901', 100.0)
    force_state = module.new_sell_watch('20260901', 100.0)

    assert module.advance_sell_watch(
        stop_state, '20260902', '09:45:00', 98.49) == 'STOP_LOSS'
    assert module.advance_sell_watch(
        force_state, '20260902', '10:00:00', 100.20) == 'FORCE_1000'

