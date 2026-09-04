import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v3_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v3', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class _IndexWeightFallbackXtdata:
    def get_stock_list_in_sector(self, sector_name):
        return []

    def get_index_weight(self, index_code):
        return {'000001.SZ': 0.2, '600000.SH': 0.3}


class _RefreshFallbackXtdata:
    def __init__(self):
        self.refreshed = False

    def get_stock_list_in_sector(self, sector_name):
        return ['000001.SZ'] if self.refreshed else []

    def get_index_weight(self, index_code):
        return {}


def test_empty_sector_falls_back_to_cached_index_weights():
    module = _load_module()
    refresh_called = []

    codes, source = module.discover_universe(
        _IndexWeightFallbackXtdata(),
        refresh_sector=lambda: refresh_called.append(True),
    )

    assert codes == ['000001.SZ', '600000.SH']
    assert source == 'cached index weights'
    assert refresh_called == []


def test_empty_local_sources_use_bounded_refresh_then_retry():
    module = _load_module()
    xtdata = _RefreshFallbackXtdata()

    def refresh():
        xtdata.refreshed = True
        return True

    codes, source = module.discover_universe(xtdata, refresh_sector=refresh)

    assert codes == ['000001.SZ']
    assert source == 'refreshed sector cache'


def test_sector_refresh_timeout_returns_without_hanging():
    module = _load_module()
    original_run = module.subprocess.run

    def timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    module.subprocess.run = timeout
    try:
        assert module._refresh_sector_with_timeout() is False
    finally:
        module.subprocess.run = original_run
