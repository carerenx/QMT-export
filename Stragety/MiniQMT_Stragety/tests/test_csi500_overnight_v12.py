import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Overnight_v12_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_overnight_v12', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_next_day_drop_does_not_trigger_removed_stop_loss():
    module = _load_module()
    state = module.new_sell_watch('20260901', 100.0)

    assert module.advance_sell_watch(
        state, '20260902', '09:31:00', 90.0) is None
    assert module.advance_sell_watch(
        state, '20260902', '10:00:00', 91.0) == 'FORCE_1000'


def test_sellback_mechanism_is_still_enabled():
    module = _load_module()
    state = module.new_sell_watch('20260901', 100.0)

    assert module.advance_sell_watch(
        state, '20260902', '09:35:00', 101.2) is None
    assert module.advance_sell_watch(
        state, '20260902', '09:36:00', 101.3) is None
    assert module.advance_sell_watch(
        state, '20260902', '09:37:00', 101.19) == 'SELLBACK'

