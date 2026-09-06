import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v4_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v4', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class _EmptyLocalUniverseXtdata:
    def get_stock_list_in_sector(self, sector_name):
        return []

    def get_index_weight(self, index_code):
        return {}

    def download_sector_data(self):
        raise AssertionError('v4 must not call the hanging sector download API')


def test_bundled_snapshot_contains_500_qmt_codes():
    module = _load_module()

    codes = module.load_bundled_constituents()

    assert len(codes) == 500
    assert len(set(codes)) == 500
    assert '601869.SH' in codes
    assert all(code.endswith(('.SH', '.SZ')) for code in codes)


def test_empty_qmt_universe_falls_back_to_bundled_snapshot_without_refresh():
    module = _load_module()

    codes, source = module.discover_universe(_EmptyLocalUniverseXtdata())

    assert len(codes) == 500
    assert source == 'bundled snapshot 2026-08-31'

