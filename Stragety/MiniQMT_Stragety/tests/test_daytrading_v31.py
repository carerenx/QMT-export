# -*- coding: utf-8 -*-
import sys
import types
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


STRATEGY_DIR = Path(__file__).resolve().parents[1]
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

import DayTradeing_v31_stragety_miniqmt as strategy
from infra.logger import set_logger
from infra.connector import MiniQMTConnector


class NullLogger:
    def write(self, *args, **kwargs):
        pass

    def close(self):
        pass


def account_snapshot(shares):
    return {
        'shares': shares,
        'can_use': shares,
        'cash': 100000.0,
        'cost': 300.0,
        'total_asset': 100000.0,
        'price': 300.0,
        'valid': True,
    }


class FakeOrderConnector:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.cancelled = []
        self.order_pending = True

    def get_order_snapshot(self, order_id):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True


class DayTradingV31Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        set_logger(NullLogger())

    def make_runner(self):
        runner = strategy.StrategyRunner(dry_run=True, restore_state=False)
        runner._init_state()
        return runner

    def test_momentum_is_disabled_by_default(self):
        self.assertFalse(strategy.MOM_ENABLED)

    def test_partial_fill_cancels_remainder_and_returns_actual_quantity(self):
        runner = self.make_runner()
        runner.dry_run = False
        runner.conn = FakeOrderConnector([
            {
                'order_id': 42, 'status': 'PARTIAL', 'terminal': False,
                'rejected': False, 'order_volume': 100,
                'traded_volume': 50, 'traded_price': 301.0,
            },
            {
                'order_id': 42, 'status': 'PART_CANCEL', 'terminal': True,
                'rejected': False, 'order_volume': 100,
                'traded_volume': 50, 'traded_price': 301.0,
            },
        ])
        runner._snapshot_account = lambda: account_snapshot(150)

        status, delta = runner._wait_for_fill(
            account_snapshot(200), -100, 'test', 301.0, -100,
            timeout_sec=0, order_id=42,
        )

        self.assertEqual(('PARTIAL', -50), (status, delta))
        self.assertEqual([42], runner.conn.cancelled)

    def test_force_buyback_keeps_open_leg_when_nothing_fills(self):
        runner = self.make_runner()
        runner.st['short_legs'] = [(400.0, 100)]
        runner.st['fstate'] = strategy.STATE_SOLD
        runner._cur_price = lambda: 410.0
        runner._submit_order = lambda *args, **kwargs: ('TIMEOUT', 0)

        runner._force_buyback()

        self.assertEqual(strategy.STATE_SOLD, runner.st['fstate'])
        self.assertEqual([(400.0, 100)], runner.st['short_legs'])

    def test_partial_buyback_preserves_remaining_short_leg(self):
        runner = self.make_runner()
        runner.st['short_legs'] = [(400.0, 100)]
        runner.st['fstate'] = strategy.STATE_SOLD
        runner._submit_order = lambda *args, **kwargs: ('PARTIAL', 50)

        bought = runner._do_buyback(390.0, 'TEST')

        self.assertEqual(50, bought)
        self.assertEqual(strategy.STATE_SOLD, runner.st['fstate'])
        self.assertEqual([(400.0, 50)], runner.st['short_legs'])

    def test_partial_force_sell_preserves_remaining_long_leg(self):
        runner = self.make_runner()
        runner.st['long_legs'] = [(300.0, 100)]
        runner.st['fstate'] = strategy.STATE_BT_BOUGHT
        runner._cur_price = lambda: 310.0
        runner._submit_order = lambda *args, **kwargs: ('PARTIAL', -50)

        runner._do_bt_force_sell()

        self.assertEqual(strategy.STATE_BT_BOUGHT, runner.st['fstate'])
        self.assertEqual([(300.0, 50)], runner.st['long_legs'])

    def test_momentum_partial_buyback_tracks_remaining_leg(self):
        runner = self.make_runner()
        runner.st.update({
            'mom_state': 'MOM_DIPPING', 'mom_sell_price': 400.0,
            'mom_dip': 390.0, 'mom_leg_shares': 100,
        })
        runner._submit_order = lambda *args, **kwargs: ('PARTIAL', 50)

        runner._mom_handle_dipping(391.0)

        self.assertEqual('MOM_SOLD', runner.st['mom_state'])
        self.assertEqual(50, runner.st['mom_leg_shares'])

    def test_completed_closes_are_not_removed_by_equal_prices(self):
        closes = strategy.StrategyRunner._completed_closes_for_ma(
            [8.0, 9.0, 10.0, 10.0], last_close=10.0)
        self.assertEqual([8.0, 9.0, 10.0, 10.0], closes)

    def test_opening_long_is_limited_by_unreserved_sellable_inventory(self):
        runner = self.make_runner()
        runner.st['base_can_use'] = 100
        runner.st['long_legs'] = [(300.0, 100)]
        runner._available_cash = lambda: 1000000.0

        allowed = runner._clamp_order_shares(100, 300.0, 'MOM long')

        self.assertEqual(0, allowed)

    def test_momentum_short_uses_sellable_inventory_not_cash(self):
        runner = self.make_runner()
        runner.st['base_can_use'] = 0
        runner._available_cash = lambda: 1000000.0

        allowed = runner._clamp_order_shares(100, 300.0, 'MOM short')

        self.assertEqual(0, allowed)

    def test_recovery_guard_allows_a_closing_order(self):
        runner = self.make_runner()
        runner.st['reconcile_required'] = True
        runner.st['reconcile_reason'] = '进程重启后恢复未平腿，请先核对账户'
        runner.st['base_can_use'] = 100

        status, delta = runner._submit_order(-100, 300.0, 'FWD-T sell')

        self.assertEqual(('FILLED', -100), (status, delta))

    def test_strength_lock_honours_minimum_cooldown(self):
        runner = self.make_runner()
        runner.st['daily_signal'] = {'open_price': 100.0}
        for i in range(10):
            runner._assess_strength(100.0 + i * 0.25, 1000.0 + i)
        self.assertTrue(runner.st['locked'])

        runner._assess_strength(100.5, 1010.0)

        self.assertTrue(runner.st['locked'])

    def test_short_stop_loss_uses_unrealized_leg_pnl(self):
        runner = self.make_runner()
        runner.st['fstate'] = strategy.STATE_SOLD
        runner.st['short_legs'] = [(100.0, 100)]
        called = []
        runner._force_buyback = lambda: called.append(True)

        runner._check_short_stop_loss(102.0)

        self.assertEqual([True], called)
        self.assertEqual(-200.0, runner.st['day_pnl'])

    def test_compete_order_maps_to_peer_price_and_tracks_order_id(self):
        constants = types.SimpleNamespace(
            STOCK_BUY=23, STOCK_SELL=24, FIX_PRICE=11, LATEST_PRICE=12,
            MARKET_PEER_PRICE_FIRST=13, MARKET_SH_CONVERT_5_CANCEL=14,
            MARKET_SZ_CONVERT_5_CANCEL=15,
        )
        fake_xtquant = types.SimpleNamespace(xtconstant=constants)
        calls = []

        class Trader:
            def order_stock(self, *args):
                calls.append(args)
                return 99

        connector = MiniQMTConnector()
        connector._trade_connected = True
        connector._account_obj = object()
        connector.trader = Trader()
        with patch.dict(sys.modules, {'xtquant': fake_xtquant}):
            order_id = connector.order_stock('601869.SH', -100, 'COMPETE', 300.0)

        self.assertEqual(99, order_id)
        self.assertEqual(99, connector.last_order_id)
        self.assertTrue(connector.order_pending)
        self.assertEqual(constants.MARKET_PEER_PRICE_FIRST, calls[0][4])
        self.assertEqual(0, calls[0][5])

    def test_open_leg_state_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = str(Path(tmpdir) / 'state.json')
            runner = self.make_runner()
            runner._state_persistence_enabled = True
            runner._state_path = state_path
            runner.st.update({
                'trade_date': datetime.now().strftime('%Y%m%d'),
                'initialized': True,
                'fstate': strategy.STATE_SOLD,
                'short_legs': [(400.0, 100)],
            })
            runner._persist_runtime_state(force=True)

            restored = self.make_runner()
            restored._state_persistence_enabled = True
            restored._state_path = state_path
            restored._init_state()

            self.assertEqual(strategy.STATE_SOLD, restored.st['fstate'])
            self.assertEqual([(400.0, 100)], restored.st['short_legs'])
            self.assertTrue(restored.st['reconcile_required'])


if __name__ == '__main__':
    unittest.main()
