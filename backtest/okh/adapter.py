# coding: utf-8
"""
OKH Adapter — QMT Strategy Format → OSkhQuant Strategy Format Bridge
=====================================================================
Wraps a QMT-format strategy so it can run on the OkhEngine.

The adapter:
  1. Creates a custom MockContextInfo backed by DataProvider
  2. Intercepts passorder()/order_shares() → translates to OKH signal dicts
  3. Routes get_trade_detail_data() → OKH trade_mgr account/position state
  4. Provides init(stocks, data) and khHandlebar(data) for OkhEngine
"""
import sys
import os
import logging
from typing import Dict, List

import numpy as np
import pandas as pd

BACKTEST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKTEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger(__name__)


class OkhMockContext:
    """Custom MockContextInfo backed by DataProvider + OkhEngine state."""

    def __init__(self, data_provider):
        self._data = data_provider
        self._barpos = 0
        self.universe = []
        self.stock_pool = []
        self.acc_id = 'testS'
        self.capital = 10_000_000
        self.benchmark = '000905.SH'

    @property
    def barpos(self):
        return self._barpos

    @barpos.setter
    def barpos(self, value):
        self._barpos = value

    def set_universe(self, stock_list):
        self.universe = list(stock_list)

    def get_universe(self):
        return self.universe

    def get_history_data(self, length, period, field, dividend_type=0, skip_paused=True):
        """Return {code: [val, ...]} for last `length` bars up to current barpos."""
        result = {}
        bar = self._barpos
        for code in self._data.code_list:
            series = self._data.get_field_series(code, field)
            if series is not None and len(series) > 0:
                start = max(0, bar + 1 - length)
                vals = list(series[start:bar + 1])
                vals = [float(v) if v is not None and not np.isnan(v) else 0.0 for v in vals]
                result[code] = vals
        return result

    def get_full_tick(self, codes):
        """Return {code: {lastPrice, open, high, low, volume, amount}} for current bar."""
        result = {}
        bar = self._barpos
        for code in codes:
            px = self._data.get_value(code, 'close', bar)
            op = self._data.get_value(code, 'open', bar)
            hi = self._data.get_value(code, 'high', bar)
            lo = self._data.get_value(code, 'low', bar)
            vol = self._data.get_value(code, 'volume', bar)
            amt = self._data.get_value(code, 'amount', bar)
            result[code] = {
                'lastPrice': float(px) if px else 0.0,
                'open': float(op) if op else 0.0,
                'high': float(hi) if hi else 0.0,
                'low': float(lo) if lo else 0.0,
                'volume': float(vol) if vol else 0.0,
                'amount': float(amt) if amt else 0.0,
            }
        return result

    def get_stock_name(self, code):
        """Return stock name or None."""
        return self._data.get_stock_name(code)

    def get_bar_timetag(self, barpos=None):
        """Return ms timestamp for bar position."""
        idx = barpos if barpos is not None else self._barpos
        try:
            date = self._data.dates_list[idx]
            import calendar
            return int(calendar.timegm(date.timetuple()) * 1000)
        except Exception:
            return 0

    def get_sector(self, sector, realtime=None):
        """Return stock pool (simplified — returns all stocks in pool)."""
        return list(self.stock_pool)

    def get_stock_list_in_sector(self, sectorname, realtime=None):
        """Return stock pool by sector name."""
        return list(self.stock_pool)

    def set_slippage(self, *args, **kwargs):
        pass

    def set_commission(self, *args, **kwargs):
        pass

    def set_account(self, acc_id):
        self.acc_id = acc_id

    def get_weight_in_index(self, indexcode, stockcode):
        return 0.0

    def get_contract_multiplier(self, *args):
        return 1


class QmtStrategyAdapter:
    """Adapts a QMT-format strategy to OSkhQuant engine format."""

    def __init__(self, strategy_path: str, data_provider, config):
        self.strategy_path = strategy_path
        self.data = data_provider
        self.config = config
        self._signals: List[dict] = []
        self._context: OkhMockContext = None
        self._engine_ref = None  # Set by engine before run

    def load(self) -> dict:
        """Load QMT strategy and return module dict with OSkhQuant entry points.

        Returns dict with keys: 'init', 'khHandlebar'
        """
        if not os.path.exists(self.strategy_path):
            raise FileNotFoundError(f"策略文件不存在: {self.strategy_path}")

        for enc in ['gbk', 'utf-8']:
            try:
                with open(self.strategy_path, 'r', encoding=enc) as f:
                    source = f.read()
                break
            except UnicodeDecodeError:
                continue

        code = compile(source, self.strategy_path, 'exec')

        adapter_self = self

        # ── Create wrapped passorder that collects signals ──
        def wrapped_passorder(opType, orderType, accountid, orderCode, prType,
                              modelprice, volume, strategyName='', quickTrade=0,
                              userOrderId='', ContextInfo=None):
            """Intercepted passorder — collects signal, does NOT call original."""
            if volume <= 0:
                return
            # opType: 23=buy, 24=sell
            action = 'buy' if opType == 23 else 'sell'
            shares = int(abs(volume))
            if shares < 100:
                return

            # Determine price
            price = modelprice if modelprice > 0 else 0.0
            if price <= 0 and ContextInfo:
                tick = ContextInfo.get_full_tick([orderCode])
                info = tick.get(orderCode, {})
                price = info.get('lastPrice', 0.0)

            adapter_self._signals.append({
                'code': orderCode,
                'action': action,
                'price': price,
                'volume': shares,
                'reason': str(strategyName) if strategyName else f'passorder {action}',
            })

        # ── Create wrapped order_shares that collects signals ──
        def wrapped_order_shares(stockcode, shares, style='LATEST', price=None,
                                 ContextInfo=None, accId=None):
            """Intercepted order_shares — collects signal, does NOT call original."""
            # Handle calling convention: price param may actually be ContextInfo
            if price is not None and not isinstance(price, (int, float)):
                accId = ContextInfo
                ContextInfo = price
                price = None

            if shares == 0:
                return
            action = 'buy' if shares > 0 else 'sell'
            abs_shares = abs(shares)

            exec_price = price if (price is not None and price > 0) else 0.0
            if exec_price <= 0 and ContextInfo:
                tick = ContextInfo.get_full_tick([stockcode])
                info = tick.get(stockcode, {})
                exec_price = info.get('lastPrice', 0.0)

            adapter_self._signals.append({
                'code': stockcode,
                'action': action,
                'price': exec_price,
                'volume': abs_shares,
                'reason': f'order_shares {action}',
            })

        # ── Create wrapped get_trade_detail_data that reads OKH engine ──
        def wrapped_get_trade_detail_data(account_id, account_type, data_type):
            """Route to OKH engine's trade_mgr for account/position state."""
            eng = adapter_self._engine_ref
            if eng is None:
                return []

            data_type = data_type.upper() if isinstance(data_type, str) else data_type

            if data_type == 'ACCOUNT':
                tm = eng.trade_mgr
                return [_OkhMockAccount(tm)]

            elif data_type == 'POSITION':
                tm = eng.trade_mgr
                result = []
                for code, pos in tm.positions.items():
                    result.append(_OkhMockPosition(code, pos))
                return result

            return []

        # ── timetag_to_datetime wrapper ──
        def wrapped_timetag_to_datetime(timetag, fmt='%Y-%m-%d %H:%M'):
            try:
                import datetime as dt
                if timetag > 1e12:
                    timetag = timetag / 1000
                return dt.datetime.utcfromtimestamp(timetag).strftime(fmt)
            except Exception:
                return str(timetag)

        # ── Build module namespace ──
        module_globals = {
            '__builtins__': __builtins__,
            '__name__': 'adapted_strategy',
            '__file__': self.strategy_path,
            '__doc__': None,
            'np': np,
            'passorder': wrapped_passorder,
            'order_shares': wrapped_order_shares,
            'get_trade_detail_data': wrapped_get_trade_detail_data,
            'timetag_to_datetime': wrapped_timetag_to_datetime,
        }

        exec(code, module_globals)

        qmt_init = module_globals.get('init')
        qmt_handlebar = module_globals.get('handlebar')

        if qmt_handlebar is None:
            raise ValueError("策略文件缺少 handlebar() 函数")

        # ── Build adapter entry points ──
        def adapted_init(stocks=None, data=None):
            """OSkhQuant-compatible init()"""
            context = OkhMockContext(self.data)
            context.stock_pool = stocks or list(self.data.code_list)
            adapter_self._context = context

            print(f"[适配器] 股票池: {len(context.stock_pool)} 只")
            if qmt_init:
                try:
                    qmt_init(context)
                    print("[适配器] QMT init() 完成")
                except Exception as e:
                    print(f"[适配器] init() 异常: {e}")
                    import traceback
                    traceback.print_exc()

        def adapted_khHandlebar(data: dict) -> List[dict]:
            """OSkhQuant-compatible khHandlebar() — returns signal dicts"""
            adapter_self._signals = []

            ctx = adapter_self._context
            if ctx is None:
                return []

            # Sync barpos from OKH engine
            if adapter_self._engine_ref:
                ctx.barpos = adapter_self._engine_ref._current_bar

            try:
                qmt_handlebar(ctx)
            except Exception as e:
                print(f"[适配器] handlebar() 异常 @ bar={ctx.barpos}: {e}")
                import traceback
                traceback.print_exc()

            return adapter_self._signals

        return {
            'init': adapted_init,
            'khHandlebar': adapted_khHandlebar,
        }


# ═══════════════════════════════════════════════════════════════
# Mock objects for get_trade_detail_data
# ═══════════════════════════════════════════════════════════════

class _OkhMockAccount:
    """Mock QMT Account object backed by OKH trade_mgr."""
    def __init__(self, trade_mgr):
        self.m_dAvailable = trade_mgr.assets['cash']
        self.m_dBalance = trade_mgr.assets['total_asset']


class _OkhMockPosition:
    """Mock QMT Position object backed by OKH trade_mgr position."""
    def __init__(self, code, pos):
        self.m_strInstrumentID = code.split('.')[0] if '.' in code else code
        self.m_strExchangeID = code.split('.')[1] if '.' in code else 'SZ'
        self.m_nVolume = pos.get('volume', 0) if pos.get('volume', 0) > 0 else 0
        self.m_dOpenPrice = pos.get('avg_price', 0)
