import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v14_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v14', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _screened_watch(module):
    watch = module.v11.new_buy_watch(100.0, 96.0, '14:00:00')
    watch.update({
        'screen_date': '20260901',
        'screen_price': 96.0,
        'name': 'TEST',
    })
    return watch


def test_screen_day_does_not_activate_buy_watch():
    module = _load_module()
    watch = _screened_watch(module)

    status = module.prepare_delayed_watch(
        watch, '20260901', 95.0, 94.0, '14:30:00')

    assert status == 'WAIT'
    assert 'activation_date' not in watch


def test_next_session_resets_buy_mechanism_with_new_open():
    module = _load_module()
    watch = _screened_watch(module)

    status = module.prepare_delayed_watch(
        watch, '20260902', 90.0, 89.0, '09:31:00')

    assert status == 'ACTIVATED'
    assert watch['activation_date'] == '20260902'
    assert abs(watch['buy_trigger_floor'] - 87.3) < 1e-12
    assert watch['screen_date'] == '20260901'


def test_unfilled_watch_expires_after_entry_session():
    module = _load_module()
    watch = _screened_watch(module)
    module.prepare_delayed_watch(
        watch, '20260902', 90.0, 89.0, '09:31:00')

    assert module.prepare_delayed_watch(
        watch, '20260903', 91.0, 90.0, '09:31:00') == 'EXPIRE'


def test_missed_next_trading_session_is_not_reactivated_after_restart():
    module = _load_module()

    assert module.entry_session_status(
        '20260901', '20260903',
        ['20260831', '20260901', '20260902', '20260903']) == 'EXPIRE'
