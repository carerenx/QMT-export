import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'dayt_v45', ROOT / 'Stragety/MiniQMT_Stragety/DayT/DayT_v45_PortfolioT.py')
strategy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(strategy)


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = strategy.PortfolioRunner(dry_run=True)
        self.logs = patch.object(strategy, '_log').start()
        self.addCleanup(patch.stopall)

    def test_holdings_discovery_refresh_and_independent_states(self):
        holdings = [SimpleNamespace(stock_code=code, volume=200)
                    for code in ('601869.SH', '000001.SZ')]
        self.portfolio.conn.trader = SimpleNamespace(
            query_stock_positions=lambda account: holdings)
        self.portfolio.refresh_holdings(0)
        a, b = self.portfolio.runners.values()
        a._init_state()
        b._init_state()
        a.st['short_legs'].append((400, 100))
        self.assertEqual(b.st['short_legs'], [])
        self.assertEqual(self.portfolio.reserved_cash('000001.SZ'), 40400)
        self.assertEqual(self.portfolio.reserved_cash('601869.SH'), 0)
        holdings.append(SimpleNamespace(stock_code='688001.SH', volume=300))
        self.portfolio.refresh_holdings(30)
        self.assertEqual(len(self.portfolio.runners), 2)
        self.portfolio.refresh_holdings(60)
        self.assertEqual(self.portfolio.runners['688001.SH'].trade_lot, 200)
        holdings.clear()
        self.portfolio.refresh_holdings(120)
        self.assertIn('601869.SH', self.portfolio.runners)
        self.assertEqual(a.st['short_legs'], [(400, 100)])

    def test_other_symbols_buyback_reserve_reduces_available_cash(self):
        a = strategy.StrategyRunner(self.portfolio, '601869.SH')
        b = strategy.StrategyRunner(self.portfolio, '000001.SZ')
        a._init_state()
        b._init_state()
        self.portfolio.runners = {a.stock_qmt: a, b.stock_qmt: b}
        a.st['short_legs'] = [(400, 100)]
        with patch.object(strategy, 'get_trade_detail_data', return_value=[
                SimpleNamespace(m_dAvailable=41000)]):
            self.assertEqual(b._available_cash(), 600)
            self.assertLess(b._clamp_buy_shares(100, 10), b.trade_lot)
            self.assertEqual(a._available_cash(), 41000)

    def test_snapshot_uses_requested_symbol_and_separate_cache(self):
        requested = []

        def get_local_data(**kwargs):
            code = kwargs['stock_list'][0]
            requested.append(code)
            price = 400.0 if code == '601869.SH' else 10.0
            frame = pd.DataFrame({
                'open': [price], 'high': [price + 1], 'low': [price - 1],
                'close': [price], 'volume': [100], 'amount': [price * 100],
            }, index=['20260908'])
            return {code: frame}

        shared = SimpleNamespace(xtdata=SimpleNamespace(
            download_history_data=lambda *args, **kwargs: None,
            get_local_data=get_local_data,
            get_trading_dates=lambda *args, **kwargs: ['20260907', '20260908']))
        a = strategy.SymbolConnector(shared, '601869.SH')
        b = strategy.SymbolConnector(shared, '000001.SZ')
        first = a.load_daily_snapshot(80, today='20260908', now_hms='19:00:00', retries=1)
        second = b.load_daily_snapshot(80, today='20260908', now_hms='19:00:00', retries=1)
        self.assertEqual(first['raw'].iloc[-1]['close'], 400)
        self.assertEqual(second['raw'].iloc[-1]['close'], 10)
        self.assertEqual(requested, ['601869.SH'] * 2 + ['000001.SZ'] * 2)
        b.refresh_daily_cache()
        self.assertIsNotNone(a._daily_data_cache)

    def test_cooperative_workers_yield_and_do_not_close_shared_connection(self):
        runner = strategy.StrategyRunner(self.portfolio, '000001.SZ')
        with patch.object(runner, '_daily_init'), patch.object(runner, '_monitor_connections'), \
                patch.object(strategy.cfg, 'is_market_open', return_value=True), \
                patch.object(runner.ctx, 'get_full_tick', return_value={}), \
                patch.object(self.portfolio.conn, 'disconnect') as disconnect:
            task = runner.run()
            self.assertEqual(next(task), 1)
            task.close()
            disconnect.assert_not_called()

    def test_lot_capacity_respects_symbol_configuration(self):
        result = strategy.calculate_execution_capacity(100, 100000, 10, 5, 200)
        self.assertFalse(result['can_short'])
        self.assertFalse(result['can_long'])

    def test_signal_mode_cannot_submit_or_simulate_a_fill(self):
        runner = strategy.StrategyRunner(self.portfolio, '000001.SZ')
        with patch.object(strategy, 'order_shares') as submit:
            self.assertEqual(runner._submit_order(-100, 10, 'REV-T sell'), ('SKIP', 0))
            submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
