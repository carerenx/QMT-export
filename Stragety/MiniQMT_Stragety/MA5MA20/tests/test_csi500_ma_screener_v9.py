import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'CSI500_MA5_MA20_Screener_v9_miniqmt.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('csi500_ma_screener_v9', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_result_columns_put_vs_ma5_after_ma5_and_add_vs_ma20():
    module = _load_module()
    result = {
        'price': 391.57,
        'ma5': 408.07,
        'ma20': 375.45,
        'gap_pct': -4.04,
    }

    line = module.format_result_line('601869.SH', '长飞光纤', result)

    assert line.index('408.07') < line.index('-4.04%')
    assert line.index('-4.04%') < line.index('375.45')
    assert line.index('375.45') < line.index('+4.29%')


def test_header_has_requested_column_order():
    module = _load_module()

    assert module.RESULT_HEADER.split() == [
        'code', 'name', 'price', 'MA5', 'vs_MA5', 'MA20', 'vs_MA20'
    ]

