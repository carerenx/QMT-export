#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MiniQMT 报价风格映射的无实盘委托测试。"""

import os
import sys
import types
import unittest


STRATEGY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if STRATEGY_DIR not in sys.path:
    sys.path.insert(0, STRATEGY_DIR)

from infra.connector import MiniQMTConnector

# Pricing-style mapping is pure adapter behavior.  Use a tiny SDK-shaped
# constant module when MiniQMT is not installed so this test remains offline.
if 'xtquant' not in sys.modules:
    xtquant = types.ModuleType('xtquant')
    xtquant.xtconstant = types.SimpleNamespace(
        STOCK_BUY=23,
        STOCK_SELL=24,
        MARKET_PEER_PRICE_FIRST=11,
        LATEST_PRICE=12,
        FIX_PRICE=13,
    )
    sys.modules['xtquant'] = xtquant
from xtquant import xtconstant


class _FakeTrader:
    def __init__(self):
        self.calls = []

    def order_stock(self, *args):
        self.calls.append(args)
        return 123456


class _FakeAccount:
    pass


class ConnectorOrderStyleTest(unittest.TestCase):
    def setUp(self):
        self.connector = MiniQMTConnector.__new__(MiniQMTConnector)
        self.connector._trade_connected = True
        self.connector.trader = _FakeTrader()
        self.connector._account_obj = _FakeAccount()
        self.connector.order_pending = False
        self.connector.last_order_info = None

    def _last_price_args(self):
        call = self.connector.trader.calls[-1]
        return call[4], call[5]

    def test_compete_uses_native_peer_price_and_zero_price(self):
        order_id = self.connector.order_stock('601869.SH', 100, 'COMPETE', 354.29)

        self.assertEqual(123456, order_id)
        self.assertEqual(
            (xtconstant.MARKET_PEER_PRICE_FIRST, 0),
            self._last_price_args(),
        )

    def test_latest_uses_native_latest_price_and_zero_price(self):
        self.connector.order_stock('601869.SH', -100, 'LATEST', 354.29)

        self.assertEqual(
            (xtconstant.LATEST_PRICE, 0),
            self._last_price_args(),
        )

    def test_fix_keeps_explicit_limit_price(self):
        self.connector.order_stock('601869.SH', 100, 'FIX', 354.29)

        self.assertEqual(
            (xtconstant.FIX_PRICE, 354.29),
            self._last_price_args(),
        )

    def test_unknown_style_is_rejected(self):
        with self.assertRaises(ValueError):
            self.connector.order_stock('601869.SH', 100, 'UNKNOWN', 354.29)


if __name__ == '__main__':
    unittest.main()
