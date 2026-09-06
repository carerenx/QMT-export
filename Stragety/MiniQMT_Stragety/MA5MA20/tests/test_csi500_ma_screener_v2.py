import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v2_miniqmt.py'


class _Column(list):
    def tolist(self):
        return list(self)


class _Frame:
    def __init__(self, dates, closes):
        self.columns = ['time', 'close']
        self._values = {
            'time': _Column(dates),
            'close': _Column(closes),
        }

    def __len__(self):
        return len(self._values['close'])

    def __getitem__(self, key):
        return self._values[key]


class _LocalDataOnlyXtdata:
    def __init__(self):
        dates = ['202608%02d' % day for day in range(14, 33)]
        self.frame = _Frame(dates, [80.0] * 15 + [120.0] * 4)

    def download_sector_data(self):
        raise AssertionError('screening must not download sector data')

    def download_history_data2(self, *args, **kwargs):
        raise AssertionError('screening must not download history data')

    def get_stock_list_in_sector(self, sector_name):
        return ['MATCH.SH']

    def get_local_data(self, **kwargs):
        return {'MATCH.SH': self.frame}

    def get_full_tick(self, codes):
        return {'MATCH.SH': {'lastPrice': 105.0}}


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v2', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_run_screen_uses_local_cache_without_any_download_call():
    module = _load_module()

    result = module.run_screen(_LocalDataOnlyXtdata(), today='20260903')

    assert result['universe_count'] == 1
    assert result['skipped'] == 0
    assert result['selected'][0][1] == 'MATCH.SH'
