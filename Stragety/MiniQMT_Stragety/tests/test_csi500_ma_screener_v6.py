import importlib.util
import math
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v6_miniqmt.py'


class _Column(list):
    def tolist(self):
        return list(self)


class _Frame:
    def __init__(self, dates, closes):
        self.columns = ['close']
        self.index = _Column(dates)
        self._closes = _Column(closes)

    def __len__(self):
        return len(self._closes)

    def __getitem__(self, key):
        if key == 'close':
            return self._closes
        raise KeyError(key)


class _NanFilledHistoryXtdata:
    def __init__(self):
        self.dates = ['202608%02d' % day for day in range(14, 33)]
        self.downloaded = False
        self.downloaded_codes = []

    def get_stock_list_in_sector(self, sector_name):
        return ['NAN.SZ']

    def get_local_data(self, **kwargs):
        closes = [80.0] * 15 + [120.0] * 4 if self.downloaded else [math.nan] * 19
        return {'NAN.SZ': _Frame(self.dates, closes)}

    def download_history_data2(self, codes, period, **kwargs):
        self.downloaded_codes = list(codes)
        self.downloaded = True
        kwargs['callback']({'finished': 1, 'total': 1})

    def get_full_tick(self, codes):
        return {'NAN.SZ': {'lastPrice': 105.0}}


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v6', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_nan_placeholder_rows_are_downloaded_as_missing_history():
    module = _load_module()
    xtdata = _NanFilledHistoryXtdata()

    result = module.run_screen(xtdata, today='20260903')

    assert xtdata.downloaded_codes == ['NAN.SZ']
    assert result['history_valid_count'] == 1
    assert result['quote_valid_count'] == 1
    assert result['skipped'] == 0
    assert len(result['selected']) == 1

