# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


STRATEGY_DIR = Path(__file__).resolve().parents[1]
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

from daytrading import (
    AtomicJsonStateStore,
    DayTradingEngine,
    DailyPlanBuilder,
    ExecutionResult,
    ExecutionCoordinator,
    InventoryLedger,
    OrderEffect,
    OrderIntent,
    OrderSnapshot,
    OrderStatus,
    PortfolioSnapshot,
    ResourcePolicy,
    StrategySettings,
    remaining_legs,
)
from daytrading.adapters import SimulatedExecutionAdapter
import DayTradeing_v32_stragety_miniqmt as strategy_v32
from infra.logger import set_logger


class StepClock:
    def __init__(self, step=1.0):
        self.value = 0.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


def portfolio(sellable=200, cash=100000.0, valid=True):
    return PortfolioSnapshot(200, sellable, cash, 300.0, 300.0, valid)


class ResourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ResourcePolicy(min_lot=100)

    def test_open_short_cannot_consume_reserved_inventory(self):
        intent = OrderIntent(OrderEffect.OPEN_SHORT, 200, 300.0, 'REV-T sell')
        self.assertEqual(
            100, self.policy.allowed_shares(intent, portfolio(), 100))

    def test_close_long_can_use_its_reserved_inventory(self):
        intent = OrderIntent(OrderEffect.CLOSE_LONG, 200, 300.0, 'FWD-T sell')
        self.assertEqual(
            200, self.policy.allowed_shares(intent, portfolio(), 200))

    def test_invalid_snapshot_fails_closed(self):
        intent = OrderIntent(OrderEffect.CLOSE_SHORT, 100, 300.0, 'buyback')
        self.assertEqual(
            0, self.policy.allowed_shares(intent, portfolio(valid=False)))

    def test_open_long_is_limited_by_cash_and_inventory(self):
        intent = OrderIntent(OrderEffect.OPEN_LONG, 200, 300.0, 'FWD-T buy')
        limited_cash = portfolio(sellable=200, cash=30100.0)
        self.assertEqual(
            100, self.policy.allowed_shares(intent, limited_cash, 0))


class ExecutionCoordinatorTests(unittest.TestCase):
    def test_full_fill_returns_signed_actual_quantity(self):
        adapter = SimulatedExecutionAdapter(portfolio())
        coordinator = ExecutionCoordinator(adapter)
        result = coordinator.execute(
            OrderIntent(OrderEffect.OPEN_SHORT, 100, 301.0, 'REV-T sell'))
        self.assertEqual(OrderStatus.FILLED, result.status)
        self.assertEqual(-100, result.signed_filled_shares)

    def test_partial_fill_cancels_and_preserves_actual_quantity(self):
        snapshots = {
            1: [
                OrderSnapshot(1, 100, 50, 301.0, False),
                OrderSnapshot(1, 100, 50, 301.0, True),
            ]
        }
        adapter = SimulatedExecutionAdapter(portfolio(), snapshots)
        coordinator = ExecutionCoordinator(
            adapter, timeout_sec=0, clock=StepClock(), sleeper=lambda _: None)
        result = coordinator.execute(
            OrderIntent(OrderEffect.OPEN_SHORT, 100, 301.0, 'REV-T sell'))
        self.assertEqual(OrderStatus.PARTIAL, result.status)
        self.assertEqual(-50, result.signed_filled_shares)
        self.assertEqual([1], adapter.cancelled)

    def test_unconfirmed_cancel_requires_reconciliation(self):
        snapshots = {1: [OrderSnapshot(1, 100, 0, 301.0, False)]}
        adapter = SimulatedExecutionAdapter(portfolio(), snapshots)
        coordinator = ExecutionCoordinator(
            adapter, timeout_sec=0, cancel_timeout_sec=1,
            clock=StepClock(), sleeper=lambda _: None)
        result = coordinator.execute(
            OrderIntent(OrderEffect.OPEN_SHORT, 100, 301.0, 'REV-T sell'))
        self.assertFalse(result.terminal_confirmed)
        self.assertTrue(result.reconciliation_reason)
        self.assertTrue(adapter.order_pending)


class DomainAndPersistenceTests(unittest.TestCase):
    def test_partial_close_keeps_remaining_leg(self):
        self.assertEqual(
            [(400.0, 50), (410.0, 100)],
            remaining_legs([(400.0, 100), (410.0, 100)], 50))

    def test_inventory_ledger_owns_all_long_reservations(self):
        ledger = InventoryLedger.from_runtime_state({
            'short_legs': [(420.0, 100)],
            'long_legs': [(300.0, 100)],
            'mom_state': 'MOM_BT_BOUGHT',
            'mom_leg_shares': 100,
        })
        self.assertEqual(200, ledger.reserved_sellable_shares)
        self.assertEqual(100, ledger.open_short_shares)
        self.assertEqual(200, ledger.open_long_shares)

    def test_state_store_is_date_scoped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / 'state.json')
            store = AtomicJsonStateStore(path)
            store.save({'trade_date': '20260822', 'short_legs': [[400, 100]]})
            self.assertIsNone(store.load_for_date('20260823'))
            self.assertEqual(
                [[400, 100]],
                store.load_for_date('20260822')['short_legs'])
            self.assertEqual(
                '20260822', store.load_latest()['trade_date'])
            with open(path, 'r', encoding='utf-8') as stream:
                self.assertEqual('20260822', json.load(stream)['trade_date'])


class NullLogger:
    def write(self, *args, **kwargs):
        pass

    def close(self):
        pass


class DayTradingEngineTests(unittest.TestCase):
    def setUp(self):
        set_logger(NullLogger())
        self.settings = StrategySettings()
        self.engine = DayTradingEngine(self.settings)
        self.engine.begin_session('20260823', {
            'do_short': True,
            'do_long': True,
            'sell_trigger': 100.0,
            'buy_trigger': 90.0,
            'atr_pct': 0.04,
            'open_price': 95.0,
        }, portfolio())

    def test_v32_is_a_composition_root_without_legacy_imports(self):
        source = Path(strategy_v32.__file__).read_text(encoding='utf-8')
        self.assertNotIn('DayTradeing_v30', source)
        self.assertNotIn('DayTradeing_v31', source)
        self.assertNotIn('class StrategyRunner', source)
        runtime = strategy_v32.build_runtime(
            dry_run=True, restore_state=False)
        self.assertIsInstance(runtime.engine, DayTradingEngine)

    def test_reverse_t_advances_only_after_execution(self):
        self.assertEqual([], self.engine.on_tick(101.0, 1.0, '10:00:00'))
        self.assertEqual('SPIKING', self.engine.session.fstate)
        actions = self.engine.on_tick(100.8, 2.0, '10:00:01')
        self.assertEqual('SPIKING', self.engine.session.fstate)
        self.assertEqual('REV-T sell', actions[0].label)
        self.engine.apply_execution(actions[0], ExecutionResult(
            OrderStatus.FILLED, -100, order_id=1))
        self.assertEqual('SOLD', self.engine.session.fstate)
        self.assertEqual([(100.8, 100)], self.engine.session.short_legs)

    def test_partial_reverse_buyback_preserves_fifo_leg(self):
        self.engine.session.fstate = 'DIPPING'
        self.engine.session.short_legs = [(400.0, 100)]
        intent = OrderIntent(
            OrderEffect.CLOSE_SHORT, 100, 390.0,
            'REV-T buyback(NORMAL)')
        self.engine.apply_execution(intent, ExecutionResult(
            OrderStatus.PARTIAL, 50, order_id=2))
        self.assertEqual('SOLD', self.engine.session.fstate)
        self.assertEqual([(400.0, 50)], self.engine.session.short_legs)
        self.assertEqual(500.0, self.engine.session.total_pnl)

    def test_engine_books_broker_average_fill_price(self):
        self.engine.session.fstate = 'SPIKING'
        intent = OrderIntent(
            OrderEffect.OPEN_SHORT, 100, 400.0, 'REV-T sell')
        self.engine.apply_execution(intent, ExecutionResult(
            OrderStatus.FILLED, -100, order_id=20,
            average_fill_price=401.25))
        self.assertEqual([(401.25, 100)], self.engine.session.short_legs)
        self.assertEqual(401.25, self.engine.session.sell_fill_price)

    def test_unfilled_force_close_preserves_open_leg(self):
        self.engine.session.fstate = 'SOLD'
        self.engine.session.short_legs = [(400.0, 100)]
        actions = self.engine.on_tick(410.0, 1.0, '14:57:00')
        self.engine.apply_execution(actions[0], ExecutionResult(
            OrderStatus.UNFILLED, 0, order_id=3))
        self.assertEqual('SOLD', self.engine.session.fstate)
        self.assertEqual([(400.0, 100)], self.engine.session.short_legs)

    def test_session_round_trip_restores_open_leg_with_guard(self):
        self.engine.session.fstate = 'SOLD'
        self.engine.session.short_legs = [(400.0, 100)]
        snapshot = self.engine.snapshot()
        restored = DayTradingEngine(self.settings)
        restored.restore(snapshot)
        self.assertEqual([(400.0, 100)], restored.session.short_legs)
        self.assertTrue(restored.session.reconcile_required)

    def test_momentum_is_disabled_by_default(self):
        self.assertFalse(self.settings.mom_enabled)

    def test_forward_t_full_lifecycle_uses_execution_feedback(self):
        self.engine.session.fstate = 'BT_DIPPING'
        self.engine.session.bt_dip_price = 90.0
        actions = self.engine.on_tick(90.2, 1.0, '10:00:00')
        self.assertEqual('FWD-T buy', actions[0].label)
        self.assertEqual('BT_DIPPING', self.engine.session.fstate)
        self.engine.apply_execution(actions[0], ExecutionResult(
            OrderStatus.FILLED, 100, order_id=10))
        self.assertEqual('BT_BOUGHT', self.engine.session.fstate)
        target = self.engine.session.bt_sellback_target
        self.engine.on_tick(target, 2.0, '10:01:00')
        actions = self.engine.on_tick(
            target * (1.0 - self.settings.pullback_pct * 1.1),
            3.0, '10:01:01')
        self.assertEqual('FWD-T sell', actions[0].label)
        self.engine.apply_execution(actions[0], ExecutionResult(
            OrderStatus.FILLED, -100, order_id=11))
        self.assertFalse(self.engine.session.long_legs)

    def test_daily_plan_excludes_current_unfinished_bar(self):
        historical_dates = pd.date_range('2026-05-01', periods=61, freq='B')
        rows = []
        for index in range(61):
            price = 90.0 + index * 0.1
            rows.append({
                'open': price, 'high': price + 1.0,
                'low': price - 1.0, 'close': price + 0.2,
                'volume': 100000 + index * 100,
            })
        bars = pd.DataFrame(rows, index=historical_dates)
        bars.loc[pd.Timestamp('2026-08-23')] = {
            'open': 999.0, 'high': 999.0, 'low': 999.0,
            'close': 999.0, 'volume': 999.0,
        }
        plan = DailyPlanBuilder(self.settings).build(
            bars,
            {'open': 100.0, 'lastClose': 99.0, 'lastPrice': 100.0},
            portfolio(),
            trade_date=__import__('datetime').date(2026, 8, 23))
        self.assertNotIn(999.0, plan.completed_closes)
        self.assertEqual(61, len(plan.completed_closes))

    def test_runtime_guard_blocks_opening_but_allows_known_leg_close(self):
        runtime = strategy_v32.build_runtime(
            dry_run=True, restore_state=False)
        runtime.engine.begin_session('20260823', {
            'do_short': True, 'do_long': False, 'sell_trigger': 100.0,
            'buy_trigger': 90.0, 'atr_pct': 0.04, 'open_price': 95.0,
        }, portfolio())
        runtime.engine.session.reconcile_required = True
        runtime.engine.session.reconcile_reason = (
            'open intraday leg restored after process restart')

        class FakeExecution:
            def __init__(self):
                self.calls = []

            def execute(self, intent, **kwargs):
                self.calls.append(intent)
                direction = -1 if intent.effect.is_sell else 1
                return ExecutionResult(
                    OrderStatus.FILLED, direction * intent.shares, order_id=88)

        runtime.execution = FakeExecution()
        opening = OrderIntent(OrderEffect.OPEN_SHORT, 100, 400.0, 'REV-T sell')
        result = runtime.execute(opening)
        self.assertEqual(OrderStatus.SKIPPED, result.status)
        self.assertFalse(runtime.execution.calls)

        runtime.engine.session.fstate = 'SOLD'
        runtime.engine.session.short_legs = [(400.0, 100)]
        closing = OrderIntent(
            OrderEffect.CLOSE_SHORT, 100, 390.0,
            'REV-T buyback(NORMAL)')
        result = runtime.execute(closing)
        self.assertEqual(OrderStatus.FILLED, result.status)
        self.assertEqual([closing], runtime.execution.calls)

    def test_engine_and_simulated_adapter_complete_same_reverse_t_cycle(self):
        coordinator = ExecutionCoordinator(
            SimulatedExecutionAdapter(portfolio()))
        self.engine.on_tick(101.0, 1.0, '10:00:00')
        sell = self.engine.on_tick(100.8, 2.0, '10:00:01')[0]
        self.engine.apply_execution(sell, coordinator.execute(sell))
        target = self.engine.session.buyback_target
        self.assertGreater(target, 0)
        self.assertEqual([], self.engine.on_tick(target, 3.0, '10:01:00'))
        buyback = self.engine.on_tick(
            target * (1.0 + self.settings.bounce_pct * 1.1),
            4.0, '10:01:01')[0]
        self.engine.apply_execution(buyback, coordinator.execute(buyback))
        self.assertFalse(self.engine.session.short_legs)
        self.assertEqual(1, self.engine.session.total_t_days)


if __name__ == '__main__':
    unittest.main()
