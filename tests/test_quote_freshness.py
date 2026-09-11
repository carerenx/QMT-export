import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Stragety/MiniQMT_Stragety'))
from core.connection_monitor import quote_freshness, probe_connections


class QuoteFreshnessTests(unittest.TestCase):
    def test_seconds_and_milliseconds_and_boundary(self):
        for stamp in (1700000000, 1700000000000, '1700000000', '1700000000000'):
            quote = dict(lastPrice=10, time=stamp)
            self.assertEqual(quote_freshness(quote, 1700000090, True)['status'], 'FRESH')
            self.assertEqual(quote_freshness(quote, 1700000091, True)['status'], 'STALE')

    def test_missing_invalid_future_and_off_hours(self):
        for stamp in (None, 0, -1, True, float('nan'), float('inf'), 'bad', '20260910103000'):
            self.assertEqual(quote_freshness(dict(lastPrice=10, time=stamp), 1700000000, True)['status'], 'UNKNOWN')
        self.assertEqual(quote_freshness(dict(lastPrice=10, time=1700000006), 1700000000, True)['status'], 'CLOCK_SKEW')
        self.assertEqual(quote_freshness({}, 1700000000, True)['status'], 'UNAVAILABLE')
        self.assertEqual(quote_freshness({}, 1700000000, False)['status'], 'NOT_APPLICABLE')

    def test_legacy_probe_and_shared_snapshot(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        quote = dict(lastPrice=10, time=1700000000)
        context = SimpleNamespace(get_full_tick=Mock(return_value={'600584.SH': quote}))
        conn = SimpleNamespace(data_connected=True, trade_connected=True, query_account=lambda: object())
        expected = (True, 'market/trade probes OK')
        self.assertEqual(probe_connections(conn, context, '600584.SH', True), expected)
        snapshot = {}
        self.assertEqual(probe_connections(conn, context, '600584.SH', True, snapshot), expected)
        self.assertIs(snapshot['quote'], quote)
        self.assertEqual(context.get_full_tick.call_count, 2)
        self.assertEqual(quote_freshness(snapshot['quote'], 1700000100, True)['status'], 'STALE')
        context.get_full_tick.side_effect = RuntimeError('offline')
        snapshot = {}
        healthy, detail = probe_connections(conn, context, '600584.SH', True, snapshot)
        self.assertFalse(healthy)
        self.assertIn('offline', detail)
        self.assertEqual(snapshot, {})


if __name__ == '__main__':
    unittest.main()
