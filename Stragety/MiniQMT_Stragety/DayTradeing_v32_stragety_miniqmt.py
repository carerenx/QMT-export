# -*- coding: gbk -*-
"""Standalone MiniQMT intraday strategy v32.

This file is a composition root. It deliberately does not import or inherit any
historical strategy version.
"""
import argparse
import os

import numpy as np

from core import config as cfg
from daytrading import (
    AtomicJsonStateStore,
    DailyPlanBuilder,
    DayTradingEngine,
    ExecutionCoordinator,
    InMemoryStateStore,
    MiniQmtRuntime,
    ResourcePolicy,
    StrategySettings,
)
from infra.logger import FileLogger, get_logger, set_logger


ACCOUNT = cfg.ACCOUNT
STOCK_CODE = cfg.STOCK_CODE
STOCK_NAME = cfg.STOCK_NAME
STOCK_QMT = cfg.STOCK_QMT
FILL_TIMEOUT_SEC = 8.0


def _json_default(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    raise TypeError('not JSON serializable: {!r}'.format(value))


def build_runtime(dry_run=False, restore_state=True):
    settings = StrategySettings.from_config(cfg)
    engine = DayTradingEngine(settings)
    plan_builder = DailyPlanBuilder(settings, cfg.HIST_DATA_LEN)
    state_path = os.path.join(
        os.path.dirname(__file__), 'state',
        'DayTradeing_v32_{}_state.json'.format(STOCK_CODE))
    state_store = (AtomicJsonStateStore(state_path, json_default=_json_default)
                   if restore_state else InMemoryStateStore())

    def execution_factory(adapter):
        return ExecutionCoordinator(
            adapter,
            resource_policy=ResourcePolicy(min_lot=settings.min_lot),
            timeout_sec=FILL_TIMEOUT_SEC,
        )

    runtime = MiniQmtRuntime(
        engine=engine,
        execution_factory=execution_factory,
        plan_builder=plan_builder,
        state_store=state_store,
        account=ACCOUNT,
        stock_code=STOCK_CODE,
        stock_qmt=STOCK_QMT,
        dry_run=dry_run,
    )
    return runtime


def main():
    parser = argparse.ArgumentParser(
        description='Standalone MiniQMT day trading v32')
    parser.add_argument('--mode', '-m', default='signal',
                        choices=['signal', 'live'])
    args = parser.parse_args()
    set_logger(FileLogger(STOCK_CODE, version='v32'))
    if args.mode == 'live':
        print('\n!!! LIVE TRADING CONFIRMATION !!!')
        print('Target: {}({}) Account: {}'.format(
            STOCK_NAME, STOCK_CODE, ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            get_logger().close()
            return
    build_runtime(dry_run=args.mode == 'signal').run()


if __name__ == '__main__':
    main()
