import importlib.util
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'HS_MainBoard_MA5_MA20_Screener_v11_NoST_ATR_bigqmt_redis.py'
SPEC = importlib.util.spec_from_file_location('screener_v11_mainboard', PATH)
strategy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(strategy)


def bars(spread):
    return [dict(high=c + spread, low=c - spread, close=c)
            for c in [8.0] * 15 + [12.0] * 4]


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def iterrows(self):
        return iter(self.rows)


class ScreenerTests(unittest.TestCase):
    def test_board_filter(self):
        for code in ['600000.SH', '601869.SH', '603001.SH', '605001.SH',
                     '000001.SZ', '001001.SZ', '002001.SZ', '003001.SZ']:
            self.assertTrue(strategy.is_main_board(code), code)
        for code in ['300001.SZ', '301001.SZ', '688001.SH', '689009.SH',
                     '900001.SH', '200001.SZ', '510300.SH', '920001.BJ', '000905.SH']:
            self.assertFalse(strategy.is_main_board(code), code)

    def test_st_and_missing_names_excluded(self):
        for name in ['ST测试', '*ST测试', 'S*ST测试', 'st测试', '', None, '-']:
            self.assertFalse(strategy.allowed_name(name), name)
        self.assertTrue(strategy.allowed_name('长飞光纤'))

    def test_st_excluded_from_final_results(self):
        class Api:
            def universe(self):
                return ['600000.SH', '600001.SH', '600002.SH'], 'fake'
            def daily_ohlc(self, codes, start, today):
                return {code: Frame([
                    ((datetime(2026, 8, 1) + timedelta(days=i)).strftime('%Y%m%d'), row)
                    for i, row in enumerate(bars(0.5))]) for code in codes}
            def ticks(self, codes):
                return {code: {'lastPrice': 10} for code in codes}
            def selected_names(self, selected):
                return {'600000.SH': '正常股票', '600001.SH': '*ST测试', '600002.SH': '-'}
        with redirect_stdout(StringIO()):
            result = strategy.run_screen(Api(), today='20260909')
        self.assertEqual([item[1] for item in result['selected']], ['600000.SH'])

    def test_dynamic_ma_and_gap_adjusted_atr(self):
        result = strategy.calculate_ma_candidate(bars(0.5), 10)
        self.assertAlmostEqual(result['ma5'], 11.6)
        self.assertAlmostEqual(result['ma20'], 8.9)
        self.assertAlmostEqual(result['atr14'], (13 + 4.5) / 14)
        self.assertTrue(result['matched'])

    def test_atr_rejects_otherwise_matching_stock_and_includes_boundary(self):
        rows = bars(0.01)
        result = strategy.calculate_ma_candidate(rows, 10)
        self.assertTrue(result['below_ma5'] and result['above_ma20'])
        self.assertFalse(result['matched'])
        self.assertTrue(strategy.calculate_ma_candidate(
            rows, 10, min_atr_percent=result['atr_percent'])['matched'])

    def test_invalid_recent_bar_not_silently_removed(self):
        for field, value in [('high', float('nan')), ('low', 0), ('close', 100)]:
            rows = bars(0.5)
            rows[-2][field] = value
            self.assertIsNone(strategy.calculate_ma_candidate(rows, 10))
        self.assertIsNone(strategy.calculate_ma_candidate(bars(0.5)[:18], 10))
        self.assertIsNone(strategy.calculate_ma_candidate(bars(0.5), 0))

    def test_today_excluded_and_dates_sorted(self):
        rows = strategy._completed_rows(Frame([
            ('20260909', dict(high=999, low=1, close=999)),
            ('20260908', dict(high=12, low=10, close=11)),
            ('20260907', dict(high=11, low=9, close=10))]), '20260909')
        self.assertEqual([r['close'] for r in rows], [10, 11])

    def test_end_to_end_outputs_only_final_intersection(self):
        class Api:
            def universe(self):
                return ['PASS.SZ', 'REJECT.SZ'], 'fake'

            def daily_ohlc(self, codes, start, today):
                return {code: Frame([
                    ((datetime(2026, 8, 1) + timedelta(days=i)).strftime('%Y%m%d'), row)
                    for i, row in enumerate(bars(0.5 if code == 'PASS.SZ' else 0.01))])
                    for code in codes}

            def ticks(self, codes):
                return {code: {'lastPrice': 10} for code in codes}

            def selected_names(self, selected):
                return {code: '测试名称' for _, code, _ in selected}

        output = StringIO()
        with redirect_stdout(output):
            result = strategy.run_screen(Api(), today='20260909')
        self.assertEqual([item[1] for item in result['selected']], ['PASS.SZ'])
        self.assertEqual(result['skipped'], 0)
        self.assertIn('测试名称', output.getvalue())
        self.assertNotIn('REJECT.SZ', output.getvalue())
        self.assertIn('ATR rejected: 1', output.getvalue())
        self.assertLess(strategy.RESULT_HEADER.index('vs_MA5'), strategy.RESULT_HEADER.index('MA20'))


if __name__ == '__main__':
    unittest.main()
