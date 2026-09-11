# -*- coding: utf-8 -*-
"""
1-minute K-line backtest runner for DayT_v51_IntradayStrength.py

Usage:
    python -m backtest.minute_backtest.run_backtest [OPTIONS]

Architecture:
    1. Load daily bars (for signal computation) and minute bars (for tick simulation)
    2. Create a mock connector that replaces MiniQMTConnector
    3. Mock time functions (datetime.now, time.time, time.monotonic)
    4. Run the strategy's generator loop, feeding minute bars as ticks
    5. Track P&L, trades, and state transitions
"""
import os, sys, time as _time, math, logging
from datetime import datetime, timedelta
from unittest.mock import patch
from collections import deque

import numpy as np
import pandas as pd

# ── Add project roots to sys.path ──
_REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STRATEGY_ROOT = os.path.join(_REPOSITORY_ROOT, 'Stragety', 'MiniQMT_Stragety')
_DAYT_ROOT = os.path.join(_STRATEGY_ROOT, 'DayT')

for root in (_REPOSITORY_ROOT, _STRATEGY_ROOT):
    if root not in sys.path:
        sys.path.insert(0, root)

from backtest.minute_backtest.data_loader import load_minute_data, load_daily_data


# ============================================================
# Mock datetime that returns synthetic backtest time
# ============================================================

class MockDatetime:
    """Mock datetime class that returns current backtest time."""
    _current = datetime(2026, 9, 10, 9, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._current

    @classmethod
    def strftime(cls, fmt):
        return cls._current.strftime(fmt)

    # Expose strptime from real datetime
    strptime = datetime.strptime


class MockTime:
    """Mock time module with synthetic timestamps."""
    _offset = 0.0
    _base_ts = 0.0

    @classmethod
    def time(cls):
        return cls._base_ts + cls._offset

    @classmethod
    def monotonic(cls):
        return cls._base_ts + cls._offset

    @classmethod
    def sleep(cls, secs):
        cls._offset += secs

    @classmethod
    def strftime(cls, fmt):
        """strftime using current mock datetime."""
        return MockDatetime._current.strftime(fmt)

    @classmethod
    def localtime(cls, ts=None):
        """localtime using current mock datetime."""
        return MockDatetime._current.timetuple()


# ============================================================
# Mock Trader (for order status queries)
# ============================================================

class MockTrader:
    """Simulates xttrader for order status queries."""

    def __init__(self, connector):
        self.conn = connector

    def query_stock_order(self, account, order_id):
        """Query order status. Returns order object or None."""
        order_id_str = str(order_id)
        if order_id_str in self.conn._filled_orders:
            return self.conn._filled_orders[order_id_str]
        if order_id_str in self.conn._pending_orders:
            return self.conn._pending_orders[order_id_str]
        return None

    def query_stock_positions(self, account):
        """Query positions."""
        if self.conn.position <= 0:
            return []
        pos = type('Pos', (), {
            'stock_code': self.conn.code,
            'volume': self.conn.position,
            'can_use_volume': self.conn.position,
            'open_price': self.conn.avg_cost,
        })()
        return [pos]

    def query_stock_orders(self, account):
        """Query pending orders."""
        return list(self.conn._pending_orders.values())

    def query_stock_trades(self, account):
        """Query filled trades."""
        return list(self.conn._filled_orders.values())


# ============================================================
# Mock Connector (replaces infra.connector.MiniQMTConnector)
# ============================================================

class MockConnector:
    """Simulates MiniQMTConnector for minute-level backtesting."""

    def __init__(self, daily_df, minute_df, code='601869.SH',
                 initial_cash=100000.0, initial_position=200):
        self.code = code
        self._daily_df = daily_df
        self._minute_df = minute_df
        self._current_idx = 0

        # Account simulation
        self.cash = initial_cash
        self.position = initial_position
        self.avg_cost = 0.0
        self.trade_log = []

        # Connector state
        self._data_connected = True
        self._trade_connected = True
        self.last_order_info = None
        self.order_pending = False

        # Order tracking
        self._order_counter = 0
        self._pending_orders = {}  # order_id -> order info
        self._filled_orders = {}   # order_id -> order info

        # Mock trader for order status queries
        self.trader = MockTrader(self)
        self._account_obj = type('AccountObj', (), {'account_id': '8890145315'})()

    def set_tick_index(self, idx):
        """Set current minute bar index for tick simulation."""
        self._current_idx = idx

    # ── Daily data for signal computation ──

    def load_daily_snapshot(self, length=80, today=None, tick_last_close=0.0,
                            tick_time=None, retries=3, retry_delay=1.0,
                            now_hms=None, stock_code=None):
        """Return daily OHLCV snapshot in the format expected by the strategy.

        Returns dict with 'adjusted' (DataFrame), 'raw' (DataFrame), and
        'last_complete_date' (str).
        """
        df = self._daily_df
        if df is None or len(df) == 0:
            return None

        # Ensure required columns are numeric
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Return last 'length' bars
        adjusted = df.tail(length).copy()
        raw = df.tail(length).copy()

        # Set index to date strings (YYYYMMDD format)
        if 'time' in adjusted.columns:
            date_idx = pd.to_datetime(adjusted['time']).dt.strftime('%Y%m%d')
            adjusted.index = date_idx.values
            raw.index = date_idx.values

        last_complete_date = adjusted.index[-1] if len(adjusted) > 0 else ''

        return {
            'adjusted': adjusted,
            'raw': raw,
            'last_complete_date': last_complete_date,
        }

    def get_history_data(self, length, period, field):
        """Return historical data as {code: [values]}."""
        if period != '1d':
            return {}
        df = self._daily_df
        if df is None or len(df) == 0:
            return {}
        col_map = {'close': 'close', 'open': 'open', 'high': 'high',
                    'low': 'low', 'volume': 'volume', 'amount': 'amount'}
        col = col_map.get(field, field)
        if col not in df.columns:
            return {}
        vals = df[col].values.tolist()[-length:]
        return {self.code: vals}

    # ── Tick data (minute bar simulation) ──

    def get_full_tick(self, codes):
        """Return current minute bar as tick data."""
        idx = self._current_df_idx
        df = self._minute_df
        if df is None or idx >= len(df):
            return {}

        row = df.iloc[idx]
        o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
        vol = float(row.get('volume', 0))
        amt = float(row.get('amount', vol * c))  # amount ≈ volume × close

        # Previous bar's close (for lastClose)
        prev_c = c
        if idx > 0:
            prev_c = float(df.iloc[idx - 1]['close'])

        tick = {
            'code': self.code,
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'lastPrice': c,
            'lastClose': prev_c,
            'volume': vol,
            'amount': amt,
            'bidPrice': [o, o, o, o, o],
            'bidVol': [100, 100, 100, 100, 100],
            'askPrice': [o, o, o, o, o],
            'askVol': [100, 100, 100, 100, 100],
            'pvolume': vol,
            'stockStatus': 1,
            'transactionNum': 0,
            'pe': 0.0, 'pb': 0.0, 'turnoverRatio': 0.0,
            'limitUp': round(c * 1.1, 2),
            'limitDown': round(c * 0.9, 2),
        }
        return {self.code: tick}

    @property
    def _current_df_idx(self):
        return self._current_idx

    # ── Account/Position queries ──

    def query_positions(self):
        """Return simulated positions."""
        if self.position <= 0:
            return []
        pos = type('MockPos', (), {
            'stock_code': self.code,
            'volume': self.position,
            'can_use_volume': self.position,
            'open_price': self.avg_cost,
            'stock_name': '长飞光纤',
        })()
        return [pos]

    def query_account(self):
        """Return simulated account asset."""
        price = self._get_current_price()
        total = self.cash + self.position * price
        asset = type('MockAsset', (), {
            'cash': self.cash,
            'total_asset': total,
            'market_value': self.position * price,
        })()
        return asset

    def _get_current_price(self):
        """Get current price from minute data."""
        idx = self._current_df_idx
        df = self._minute_df
        if df is not None and idx < len(df):
            return float(df.iloc[idx]['close'])
        return 0.0

    # ── Order execution ──

    def order_stock(self, stock_code, shares, style='FIX', price=None):
        """Execute order immediately at current price. Returns order_id."""
        exec_price = self._get_current_price()
        if exec_price <= 0:
            return None

        # Create order ID
        self._order_counter += 1
        order_id = str(self._order_counter)

        # Create order object
        order = type('Order', (), {
            'order_id': order_id,
            'order_sysid': order_id,
            'stock_code': stock_code,
            'order_status': 56,  # SUCCEEDED
            'traded_volume': 0,
            'traded_price': 0.0,
            'order_volume': abs(shares),
            'order_price': exec_price,
        })()

        if shares > 0:  # Buy
            cost = exec_price * shares * 1.00025  # commission
            if self.cash < cost:
                # Partial fill: buy what we can afford
                max_shares = int(self.cash / (exec_price * 1.00025))
                max_shares = (max_shares // 100) * 100  # round to lots
                if max_shares <= 0:
                    return None
                shares = max_shares
                cost = exec_price * shares * 1.00025
                order.order_volume = shares

            self.cash -= cost
            # Update average cost
            total_cost = self.avg_cost * self.position + exec_price * shares
            self.position += shares
            self.avg_cost = total_cost / self.position if self.position > 0 else 0

            # Update order as filled
            order.traded_volume = shares
            order.traded_price = exec_price
            self._filled_orders[order_id] = order

            self.trade_log.append({
                'time': str(MockDatetime._current),
                'action': 'BUY',
                'price': exec_price,
                'shares': shares,
                'cost': cost,
                'cash_after': self.cash,
                'position_after': self.position,
            })
            return order_id

        elif shares < 0:  # Sell
            sell_shares = abs(shares)
            if self.position < sell_shares:
                sell_shares = self.position
                order.order_volume = sell_shares
            if sell_shares <= 0:
                return None

            proceeds = exec_price * sell_shares * (1 - 0.00025 - 0.001)  # commission + stamp tax
            self.cash += proceeds
            self.position -= sell_shares

            # Update order as filled
            order.traded_volume = sell_shares
            order.traded_price = exec_price
            self._filled_orders[order_id] = order

            self.trade_log.append({
                'time': str(MockDatetime._current),
                'action': 'SELL',
                'price': exec_price,
                'shares': sell_shares,
                'proceeds': proceeds,
                'cash_after': self.cash,
                'position_after': self.position,
            })
            return order_id

        return None

    def disconnect(self):
        pass

    def cancel_order(self, order_id):
        return False


# ============================================================
# Mock data objects for get_trade_detail_data
# ============================================================

class MockPositionObj:
    def __init__(self, code, shares, avg_cost):
        self.m_strInstrumentID = code.split('.')[0]
        self.m_strExchangeID = code.split('.')[1] if '.' in code else 'SH'
        self.m_nVolume = shares
        self.m_nCanUseVolume = shares
        self.m_dOpenPrice = avg_cost


class MockAccountObj:
    def __init__(self, cash, total_asset):
        self.m_dAvailable = cash
        self.m_dBalance = total_asset


# ============================================================
# Main backtest runner
# ============================================================

def run_backtest(code='601869.SH', initial_cash=100000.0, initial_position=200,
                 date_str='20260910', verbose=True):
    """Run 1-minute backtest for DayT_v51 strategy.

    Args:
        code: Stock code in QMT format
        initial_cash: Starting cash (yuan)
        initial_position: Starting position (shares)
        date_str: Trading date to backtest (YYYYMMDD)
        verbose: Print strategy logs

    Returns:
        dict with backtest results
    """
    print(f'=== 1-Minute Backtest: {code} ===')
    print(f'Initial: cash={initial_cash:,.0f} position={initial_position} shares')

    # ── Step 1: Load data ──
    print('\n[1] Loading data...')
    daily_df = load_daily_data(code, days=400)
    if daily_df is None or len(daily_df) < 30:
        print('ERROR: Cannot load sufficient daily data')
        return None

    minute_df = load_minute_data(code, days=5)
    if minute_df is None or len(minute_df) == 0:
        print('ERROR: Cannot load minute data')
        return None

    print(f'  Daily bars: {len(daily_df)} ({daily_df.iloc[0]["time"]} ~ {daily_df.iloc[-1]["time"]})')
    print(f'  Minute bars: {len(minute_df)}')

    # Filter minute bars to target date
    # mootdx time format may vary; try to filter
    target_date = date_str  # YYYYMMDD
    try:
        # Try parsing minute bar times to filter by date
        minute_df['_parsed_time'] = pd.to_datetime(minute_df['time'], errors='coerce')
        day_mask = minute_df['_parsed_time'].dt.strftime('%Y%m%d') == target_date
        if day_mask.any():
            filtered = minute_df[day_mask].reset_index(drop=True)
            print(f'  Minute bars on {target_date}: {len(filtered)}')
            if len(filtered) > 0:
                minute_df = filtered
        else:
            print(f'  WARNING: No minute bars for {target_date}, using all data')
        minute_df = minute_df.drop(columns=['_parsed_time'], errors='ignore')
    except Exception as e:
        print(f'  WARNING: Date filtering failed ({e}), using all data')

    if len(minute_df) == 0:
        print('ERROR: No minute bars available for backtest')
        return None

    # ── Step 2: Create mock connector ──
    print('\n[2] Setting up mock connector...')
    mock_conn = MockConnector(
        daily_df=daily_df,
        minute_df=minute_df,
        code=code,
        initial_cash=initial_cash,
        initial_position=initial_position,
    )

    # ── Step 3: Patch modules ──
    print('[3] Patching modules...')
    _setup_mocks(mock_conn)

    # ── Step 4: Import strategy ──
    print('[4] Importing strategy...')
    # Clear any cached strategy imports
    mods_to_remove = [k for k in sys.modules
                      if k.startswith('DayT_v51') or k == 'DayT_v51_IntradayStrength']
    for mod in mods_to_remove:
        del sys.modules[mod]

    strategy_path = os.path.join(_DAYT_ROOT, 'DayT_v51_IntradayStrength.py')
    import importlib.util
    spec = importlib.util.spec_from_file_location('DayT_v51_IntradayStrength', strategy_path)
    strategy_mod = importlib.util.module_from_spec(spec)
    sys.modules['DayT_v51_IntradayStrength'] = strategy_mod
    spec.loader.exec_module(strategy_mod)

    # ── Step 5: Initialize strategy ──
    print('[5] Initializing strategy...')
    # Set the connector as the global connection
    from Stragety.MiniQMT_Stragety.DayT.infra import connector as conn_module
    conn_module.set_global_conn(mock_conn, dry_run=True)

    # Create PortfolioRunner in live (dry_run=False) mode for actual order execution
    runner = strategy_mod.PortfolioRunner(dry_run=False)
    runner.checkpoint_active = False  # Skip checkpoint logic

    # Create a single-symbol runner
    sr = strategy_mod.StrategyRunner(runner, code, '长飞光纤')

    # Initialize state
    sr._init_state()
    sr.execution_book = strategy_mod.ExecutionBook()

    # Daily init (computes signals)
    try:
        sr._daily_init()
        signal = sr.st.get('daily_signal')
        if signal:
            sr._print_daily_brief(signal)
    except Exception as e:
        print(f'  WARNING: daily init failed: {e}')
        import traceback
        traceback.print_exc()

    sr._restored = True

    # ── Step 6: Run minute loop ──
    print(f'\n[6] Running backtest ({len(minute_df)} minute bars)...')
    print('=' * 70)

    equity_curve = []
    bar_times = []
    state_transitions = []
    prev_state = sr.st.get('fstate', 'IDLE')

    gen = sr.run()
    total_bars = len(minute_df)

    for idx in range(total_bars):
        row = minute_df.iloc[idx]
        price = float(row['close'])

        # Parse time for this bar
        try:
            bar_time = pd.to_datetime(row['time'])
            MockDatetime._current = bar_time.to_pydatetime()
            MockTime._offset = bar_time.timestamp() - MockTime._base_ts
        except Exception:
            # If time parsing fails, use synthetic time
            minute_of_day = idx
            hour = 9 + (30 + minute_of_day) // 60
            minute = (30 + minute_of_day) % 60
            MockDatetime._current = MockDatetime._current.replace(
                hour=min(hour, 15), minute=minute, second=0)

        # Set current tick index
        mock_conn.set_tick_index(idx)

        # Advance strategy generator
        try:
            delay = next(gen)
        except StopIteration:
            print(f'\n  Strategy generator stopped at bar {idx}')
            break
        except Exception as e:
            print(f'\n  ERROR at bar {idx}: {e}')
            import traceback
            traceback.print_exc()
            break

        # Track equity
        total_value = mock_conn.cash + mock_conn.position * price
        equity_curve.append(total_value)
        bar_times.append(str(MockDatetime._current))

        # Track state transitions
        cur_state = sr.st.get('fstate', 'IDLE')
        if cur_state != prev_state:
            state_transitions.append({
                'time': str(MockDatetime._current),
                'from': prev_state,
                'to': cur_state,
                'price': price,
            })
            prev_state = cur_state

        # Progress indicator
        if (idx + 1) % 60 == 0 or idx == total_bars - 1:
            print(f'  [{idx+1}/{total_bars}] {MockDatetime._current.strftime("%H:%M")} '
                  f'price={price:.2f} state={cur_state} '
                  f'cash={mock_conn.cash:,.0f} pos={mock_conn.position} '
                  f'equity={total_value:,.0f}')

    # ── Step 7: Force close at end of day ──
    print('\n[7] End-of-day settlement...')
    final_price = float(minute_df.iloc[-1]['close'])
    if mock_conn.position > 0:
        print(f'  Closing {mock_conn.position} shares at {final_price:.2f}')
        mock_conn.order_stock(code, -mock_conn.position, 'FIX', final_price)

    # ── Step 8: Results ──
    print('\n' + '=' * 70)
    print('=== BACKTEST RESULTS ===')
    print('=' * 70)

    final_equity = mock_conn.cash
    initial_equity = initial_cash + initial_position * float(minute_df.iloc[0]['close'])
    pnl = final_equity - initial_equity
    pnl_pct = pnl / initial_equity * 100 if initial_equity > 0 else 0

    print(f'\nPeriod: {bar_times[0] if bar_times else "N/A"} ~ {bar_times[-1] if bar_times else "N/A"}')
    print(f'Bars: {total_bars}')
    print(f'\nInitial equity: {initial_equity:,.0f}')
    print(f'Final equity:   {final_equity:,.0f}')
    print(f'P&L:            {pnl:+,.0f} ({pnl_pct:+.2f}%)')
    print(f'\nFinal cash:     {mock_conn.cash:,.0f}')
    print(f'Final position: {mock_conn.position} shares')

    print(f'\nTrades: {len(mock_conn.trade_log)}')
    for t in mock_conn.trade_log:
        print(f"  {t['time']} {t['action']} {t['shares']} sh @ {t['price']:.2f}")

    print(f'\nState transitions: {len(state_transitions)}')
    for st in state_transitions:
        print(f"  {st['time']}: {st['from']} -> {st['to']} @ {st['price']:.2f}")

    # Save results
    results = {
        'initial_equity': initial_equity,
        'final_equity': final_equity,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'trades': mock_conn.trade_log,
        'state_transitions': state_transitions,
        'equity_curve': equity_curve,
        'bar_times': bar_times,
    }

    return results


def _setup_mocks(mock_conn):
    """Set up all module-level mocks for the backtest."""
    # Mock datetime in core.config
    import core.config as cfg_mod
    cfg_mod.datetime = MockDatetime

    # Mock time in connector module
    from Stragety.MiniQMT_Stragety.DayT.infra import connector as conn_mod
    conn_mod._time = MockTime

    # Replace connector's global connection
    conn_mod._global_conn = mock_conn
    conn_mod._global_dry_run = True

    # Replace get_trade_detail_data and order_shares on the module
    def mock_get_trade_detail(account_id, account_type, data_type):
        if data_type.upper() == 'POSITION':
            return [MockPositionObj(mock_conn.code, mock_conn.position, mock_conn.avg_cost)]
        elif data_type.upper() == 'ACCOUNT':
            return [MockAccountObj(mock_conn.cash, mock_conn.cash + mock_conn.position * mock_conn._get_current_price())]
        return []

    def mock_order_shares(stockcode, shares, style='LATEST', price=None, ContextInfo=None, accId=None):
        _price = price
        if price is not None and not isinstance(price, (int, float)):
            _price = None
        return mock_conn.order_stock(stockcode, shares, style, _price)

    conn_mod.get_trade_detail_data = mock_get_trade_detail
    conn_mod.order_shares = mock_order_shares

    # Also patch time references in strategy modules
    # The strategy imports 'time as _time' at module level
    # We need to patch the _time reference in the connector module
    conn_mod._time = MockTime

    # Patch time in any already-imported strategy modules
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, '_time') and hasattr(mod._time, 'time'):
            mod._time = MockTime

    # Patch MockContextInfo in connector module to use our mock
    conn_mod.MockContextInfo = type('MockContextInfo', (), {
        '__init__': lambda self, conn: setattr(self, 'conn', conn) or setattr(self, 'st', {}),
        'get_history_data': lambda self, *a, **kw: self.conn.get_history_data(*a, **kw),
        'get_full_tick': lambda self, codes: self.conn.get_full_tick(codes),
        'is_last_bar': lambda self: True,
        'run_time': lambda self, *a: None,
    })

    # Patch MiniQMTConnector class in connector module to use our mock
    # This is critical because SymbolConnector.load_daily_snapshot calls
    # MiniQMTConnector.load_daily_snapshot(self, ...) directly
    class PatchedMiniQMTConnector:
        def __init__(self, *args, **kwargs):
            self.trader = mock_conn.trader
            self._account_obj = mock_conn._account_obj

        def __getattr__(self, name):
            return getattr(mock_conn, name)

        # Explicitly delegate all methods we need
        def load_daily_snapshot(self, *args, **kwargs):
            return mock_conn.load_daily_snapshot(*args, **kwargs)

        def get_history_data(self, *args, **kwargs):
            return mock_conn.get_history_data(*args, **kwargs)

        def get_full_tick(self, *args, **kwargs):
            return mock_conn.get_full_tick(*args, **kwargs)

        def connect_data(self):
            return True

        def connect_trade(self):
            return True

        def query_positions(self):
            return mock_conn.query_positions()

        def query_account(self):
            return mock_conn.query_account()

        def order_stock(self, *args, **kwargs):
            return mock_conn.order_stock(*args, **kwargs)

        def disconnect(self):
            pass

        def cancel_order(self, *args):
            return False

        def load_daily_snapshot_force(self, *args, **kwargs):
            return mock_conn.load_daily_snapshot(*args, **kwargs)

    conn_mod.MiniQMTConnector = PatchedMiniQMTConnector


# ============================================================
# CLI entry point
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='1-minute backtest for DayT_v51')
    parser.add_argument('--code', default='601869.SH', help='Stock code')
    parser.add_argument('--cash', type=float, default=100000, help='Initial cash')
    parser.add_argument('--position', type=int, default=200, help='Initial position (shares)')
    parser.add_argument('--date', default='20260910', help='Trading date (YYYYMMDD)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')

    results = run_backtest(
        code=args.code,
        initial_cash=args.cash,
        initial_position=args.position,
        date_str=args.date,
    )
