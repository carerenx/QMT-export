import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v5_miniqmt.py'


class _Column(list):
    def tolist(self):
        return list(self)


class _Frame:
    def __init__(self, dates=(), closes=()):
        self.columns = ['close']
        self.index = _Column(dates)
        self._closes = _Column(closes)

    def __len__(self):
        return len(self._closes)

    def __getitem__(self, key):
        if key == 'close':
            return self._closes
        raise KeyError(key)


class _PartiallyCachedXtdata:
    def __init__(self):
        dates = ['202608%02d' % day for day in range(14, 33)]
        self.valid_frame = _Frame(dates, [80.0] * 15 + [120.0] * 4)
        self.downloaded = False
        self.download_request = None

    def get_stock_list_in_sector(self, sector_name):
        return ['CACHED.SH', 'MISSING.SZ']

    def get_local_data(self, **kwargs):
        return {
            'CACHED.SH': self.valid_frame,
            'MISSING.SZ': self.valid_frame if self.downloaded else _Frame(),
        }

    def download_history_data2(self, codes, period, **kwargs):
        self.download_request = (list(codes), period, kwargs)
        self.downloaded = True
        kwargs['callback']({'finished': len(codes), 'total': len(codes)})

    def get_full_tick(self, codes):
        return {code: {'lastPrice': 105.0} for code in codes}


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v5', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_missing_daily_frames_are_downloaded_then_reloaded():
    module = _load_module()
    xtdata = _PartiallyCachedXtdata()

    result = module.run_screen(xtdata, today='20260903')

    assert xtdata.download_request[0] == ['MISSING.SZ']
    assert xtdata.download_request[1] == '1d'
    assert result['history_valid_count'] == 2
    assert result['quote_valid_count'] == 2
    assert result['skipped'] == 0
    assert len(result['selected']) == 2

