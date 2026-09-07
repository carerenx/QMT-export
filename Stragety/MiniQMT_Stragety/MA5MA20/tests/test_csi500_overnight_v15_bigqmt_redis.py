import importlib.util
from pathlib import Path
import sys
import unittest
from io import StringIO
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
BRIDGE = ROOT / "integrations" / "bigqmt" / "src"
SCRIPT = Path(__file__).resolve().parents[1] / "CSI500_MA5_MA20_Overnight_v15_bigqmt_redis.py"


def load_module():
    sys.path.insert(0, str(BRIDGE))
    try:
        spec = importlib.util.spec_from_file_location("v15_bigqmt_redis", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class Frame(object):
    def __init__(self, rows): self.rows = rows
    def iterrows(self): return iter(self.rows)


class Row(dict):
    pass


class FakeApi(object):
    def __init__(self, module):
        self.module, self.calls = module, []
        self.verified = False
    def verify(self): self.verified = True
    def universe(self): return ["000001.SZ"]
    def daily_ohlc(self, codes, start, today):
        self.calls.append((tuple(codes), start, today))
        rows = [("202608{:02d}".format(day), Row(high=day + 1, low=day - 1, close=float(day))) for day in range(2, 27)]
        return {code: Frame(rows) for code in codes}
    def ticks(self, codes): return {}


class FakeXtData(object):
    def __init__(self): self.calls = []
    def get_market_data_ex(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class StrategyTests(unittest.TestCase):
    def setUp(self): self.module = load_module()

    def test_completed_bar_candidate_and_filters(self):
        rows = [(item + 1.0, item - 1.0, float(item)) for item in range(2, 27)]
        result = self.module.calculate_candidate(rows, 22.9)
        self.assertTrue(result["matched"])
        self.assertFalse(self.module.calculate_candidate(rows, 23.4)["matched"])
        falling = [(value + 1.0, value - 1.0, value) for value in [200.0] * 5 + [100.0] * 15 + [120.0] * 5]
        self.assertFalse(self.module.calculate_candidate(falling, 110.0)["matched"])

    def test_virtual_lifecycle_never_calls_order_api(self):
        api = FakeApi(self.module)
        runner = self.module.SignalRunner(api)
        runner.refresh_history("20260901")
        self.assertEqual(runner.codes, ["000001.SZ"])
        watch = self.module.new_buy_watch(100.0, 100.0, "09:30:00")
        runner.watches["000001.SZ"] = dict(watch, screen_date="20260901")
        runner.entry_allowed = True
        runner.process_entries("20260901", "09:33:01", {"000001.SZ": {"lastPrice": 96.9}})
        runner.process_entries("20260901", "09:36:02", {"000001.SZ": {"lastPrice": 97.3}})
        self.assertIn("000001.SZ", runner.positions)
        runner.process_exits("20260902", "10:00:00", {"000001.SZ": {"lastPrice": 97.0}})
        self.assertNotIn("000001.SZ", runner.positions)
        self.assertTrue(api.calls)
        self.assertFalse(hasattr(api, "order_stock"))

    def test_scan_once_prints_v13_style_candidates_outside_market_hours(self):
        api = FakeApi(self.module)
        runner = self.module.SignalRunner(api)
        runner.refresh_history("20260901")
        rows = [(item + 1.0, item - 1.0, float(item)) for item in range(2, 27)]
        runner.ohlc = {"000001.SZ": rows, self.module.INDEX_CODE: rows}
        runner.index_closes = [row[2] for row in rows]
        api.ticks = lambda codes: {
            "000001.SZ": {"lastPrice": 22.9, "open": 24.0},
            self.module.INDEX_CODE: {"lastPrice": 22.9, "open": 24.0},
        }
        output = StringIO()
        with mock.patch("sys.stdout", output):
            runner.scan_once(self.module.datetime(2026, 9, 1, 18, 0, 0))
        self.assertIn("[SELECTED] 000001.SZ", output.getvalue())
        self.assertIn("[SCREEN] candidates=1", output.getvalue())

    def test_history_is_requested_in_bounded_hundred_code_batches(self):
        api = object.__new__(self.module.BigQmtRedisApi)
        api.xtdata = FakeXtData()
        api.daily_ohlc(["00000{}.SZ".format(i) for i in range(223)], "20260101", "20260901")
        self.assertEqual([len(call["stock_list"]) for call in api.xtdata.calls], [100, 100, 23])
        self.assertTrue(all(call["count"] == self.module.HISTORY_BAR_COUNT for call in api.xtdata.calls))
        self.assertTrue(all(call["timeout_seconds"] == self.module.HISTORY_RPC_TIMEOUT_SECONDS for call in api.xtdata.calls))


if __name__ == "__main__":
    unittest.main()
