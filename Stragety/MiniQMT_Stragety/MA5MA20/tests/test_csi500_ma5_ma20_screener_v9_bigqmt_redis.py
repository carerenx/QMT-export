import importlib.util
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
STRATEGY = ROOT / "Stragety" / "MiniQMT_Stragety" / "MA5MA20" / "CSI500_MA5_MA20_Screener_v9_bigqmt_redis.py"
spec = importlib.util.spec_from_file_location("screener_v9_bigqmt", STRATEGY)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeRow(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeFrame(object):
    def __init__(self, rows):
        self.rows = rows

    def iterrows(self):
        return iter(self.rows)


class FakeApi(object):
    def __init__(self):
        self.codes = ["000001.SZ", "000002.SZ"]
        self.rows = [("20260801", FakeRow(close=10.0 + index * 0.1)) for index in range(25)]

    def universe(self):
        return self.codes, "fake"

    def daily_close(self, codes, start, today):
        return {code: FakeFrame(self.rows) for code in codes}

    def ticks(self, codes):
        return {"000001.SZ": {"lastPrice": 10.0}, "000002.SZ": {"lastPrice": 12.4}}

    def selected_names(self, selected):
        return {code: "测试" for _, code, _ in selected}


class ScreenerV9BigQmtRedisTests(unittest.TestCase):
    def test_candidate_uses_live_price_in_both_moving_averages(self):
        closes = [10.0] * 19
        result = module.calculate_ma_candidate(closes, 9.5)
        self.assertAlmostEqual(result["ma5"], 9.9)
        self.assertAlmostEqual(result["ma20"], 9.975)
        self.assertFalse(result["matched"])

    def test_candidate_matches_only_below_ma5_and_above_ma20(self):
        closes = [8.0] * 15 + [12.0] * 4
        result = module.calculate_ma_candidate(closes, 10.0)
        self.assertTrue(result["matched"])
        self.assertLess(result["gap_pct"], -3.0)

    def test_completed_closes_excludes_today_and_invalid_values(self):
        frame = FakeFrame([("20260904", FakeRow(close=10)), ("20260907", FakeRow(close=11)),
                           ("20260903", FakeRow(close=0))])
        self.assertEqual(module._completed_closes(frame, "20260907"), [10.0])

    def test_run_screen_prints_separate_v9_name_and_distance_columns(self):
        output = StringIO()
        with mock.patch("sys.stdout", output):
            result = module.run_screen(FakeApi(), today="20260907")
        self.assertEqual(result["universe_count"], 2)
        self.assertEqual(len(result["below_ma5"]), 1)
        self.assertEqual(len(result["above_ma20"]), 1)
        self.assertIn("name", output.getvalue())
        self.assertIn("vs_MA20", output.getvalue())
        self.assertIn("current price < MA5 by 3.00%", output.getvalue())
        self.assertIn("current price > MA20", output.getvalue())
        self.assertIn("combined matched:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
