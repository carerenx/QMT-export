import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v8_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v8', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class _InstrumentXtdata:
    def __init__(self):
        self.requested = []

    def get_instrument_detail(self, code):
        self.requested.append(code)
        return {'InstrumentName': '长飞光纤'}


def test_load_names_only_for_selected_stocks():
    module = _load_module()
    xtdata = _InstrumentXtdata()
    selected = [(-3.9, '601869.SH', {
        'price': 392.29,
        'ma5': 408.21,
        'ma20': 375.49,
        'gap_pct': -3.90,
    })]

    names = module.load_selected_names(xtdata, selected)

    assert names == {'601869.SH': '长飞光纤'}
    assert xtdata.requested == ['601869.SH']
    assert '长飞光纤' in module.format_result_line(
        '601869.SH', names['601869.SH'], selected[0][2])


def test_missing_instrument_name_falls_back_to_dash():
    module = _load_module()

    class EmptyXtdata:
        def get_instrument_detail(self, code):
            return None

    assert module.load_selected_names(
        EmptyXtdata(), [(0, '000001.SZ', {})]) == {'000001.SZ': '-'}

