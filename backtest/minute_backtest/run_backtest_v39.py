# -*- coding: utf-8 -*-
"""
1-minute K-line backtest runner for DayTradeing_v39_stragety_miniqmt.py

v39 uses a while-loop architecture (not a generator like v51).
This runner patches _time.sleep to inject minute bars and control time progression.

Usage:
    python -m backtest.minute_backtest.run_backtest_v39 [OPTIONS]
"""
import os, sys, time as _time, math, logging, threading, io

# Force UTF-8 stdout/stderr to avoid GBK encoding errors on Windows
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from datetime import datetime, timedelta
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

logger = logging.getLogger(__name__)

# Save the real time module before any patching
_real_time_module = _time


# ============================================================
# Mock datetime / time
# ============================================================

class MockDatetime:
    _current = datetime(2026, 9, 10, 9, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._current

    @classmethod
    def strftime(cls, fmt):
        return cls._current.strftime(fmt)

    strptime = datetime.strptime


class MockTime:
    _offset = 0.0
    _base_ts = 0.0

    @classmethod
    def time(cls):
        return cls._base_ts + cls._offset

    @classmethod
    def monotonic(cls):
        return cls._base_ts + cls._offset

    @classmethod
    def strftime(cls, fmt):
        return MockDatetime._current.strftime(fmt)

    @classmethod
    def localtime(cls, ts=None):
        return MockDatetime._current.timetuple()


# ============================================================
# Mock Trader (for order status queries)
# ============================================================

class MockTrader:
    def __init__(self, connector):
        self.conn = connector

    def query_stock_order(self, account, order_id):
        order_id_str = str(order_id)
        if order_id_str in self.conn._filled_orders:
            return self.conn._filled_orders[order_id_str]
        if order_id_str in self.conn._pending_orders:
            return self.conn._pending_orders[order_id_str]
        return None

    def query_stock_positions(self, account):
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
        return list(self.conn._pending_orders.values())

    def query_stock_trades(self, account):
        return list(self.conn._filled_orders.values())


# ============================================================
# Mock Connector
# ============================================================

class MockConnector:
    def __init__(self, daily_df, minute_df, code='601869.SH',
                 initial_cash=100000.0, initial_position=200):
        self.code = code
        self._daily_df = daily_df
        self._minute_df = minute_df
        self._current_idx = 0

        # Account
        self.cash = initial_cash
        self.position = initial_position
        self.avg_cost = 0.0
        self.trade_log = []

        # Connection state
        self._data_connected = True
        self._trade_connected = True
        self.last_order_id = None

        # Order tracking
        self._order_counter = 0
        self._pending_orders = {}
        self._filled_orders = {}

        self.trader = MockTrader(self)
        self._account_obj = type('AccountObj', (), {'account_id': '8890145315'})()

    def set_tick_index(self, idx):
        self._current_idx = idx

    # ── Daily data ──

    def load_daily_snapshot(self, length=80, today=None, tick_last_close=0.0,
                            tick_time=None, retries=3, retry_delay=1.0,
                            now_hms=None, stock_code=None):
        df = self._daily_df
        if df is None or len(df) == 0:
            return None
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        adjusted = df.tail(length).copy()
        raw = df.tail(length).copy()
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

    def load_daily_snapshot_force(self, *args, **kwargs):
        return self.load_daily_snapshot(*args, **kwargs)

    def get_history_data(self, length, period, field):
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

    # ── Tick data ──

    def get_full_tick(self, codes):
        idx = self._current_idx
        df = self._minute_df
        if df is None or idx >= len(df):
            return {}
        row = df.iloc[idx]
        o, h, l, c = float(row['open']), float(row['high']), float(row['low']), float(row['close'])
        vol = float(row.get('volume', 0))
        amt = float(row.get('amount', vol * c))
        prev_c = c
        if idx > 0:
            prev_c = float(df.iloc[idx - 1]['close'])

        tick = {
            'code': self.code,
            'open': o, 'high': h, 'low': l, 'close': c,
            'lastPrice': c, 'lastClose': prev_c,
            'volume': vol, 'amount': amt,
            'bidPrice': [o, o, o, o, o], 'bidVol': [100, 100, 100, 100, 100],
            'askPrice': [o, o, o, o, o], 'askVol': [100, 100, 100, 100, 100],
            'pvolume': vol, 'stockStatus': 1,
            'timetag': MockDatetime._current.strftime('%Y%m%d%H%M%S'),
        }
        return {self.code: tick}

    # ── Account ──

    def query_positions(self):
        if self.position <= 0:
            return []
        pos = type('MockPos', (), {
            'm_strInstrumentID': self.code.split('.')[0],
            'm_nVolume': self.position,
            'm_nCanUseVolume': self.position,
            'm_dOpenPrice': self.avg_cost,
        })()
        return [pos]

    def query_account(self):
        price = self._get_current_price()
        total = self.cash + self.position * price
        asset = type('MockAsset', (), {
            'cash': self.cash,
            'total_asset': total,
            'm_dAvailable': self.cash,
            'm_dBalance': total,
        })()
        return asset

    def _get_current_price(self):
        idx = self._current_idx
        df = self._minute_df
        if df is not None and idx < len(df):
            return float(df.iloc[idx]['close'])
        return 0.0

    # ── Orders ──

    def order_stock(self, stock_code, shares, style='FIX', price=None):
        exec_price = self._get_current_price()
        if exec_price <= 0:
            return None

        self._order_counter += 1
        order_id = str(self._order_counter)

        order = type('Order', (), {
            'order_id': order_id, 'order_sysid': order_id,
            'stock_code': stock_code, 'order_status': 56,
            'traded_volume': 0, 'traded_price': 0.0,
            'order_volume': abs(shares), 'order_price': exec_price,
        })()

        if shares > 0:  # Buy
            cost = exec_price * shares * 1.00025
            if self.cash < cost:
                max_shares = int(self.cash / (exec_price * 1.00025))
                max_shares = (max_shares // 100) * 100
                if max_shares <= 0:
                    return None
                shares = max_shares
                cost = exec_price * shares * 1.00025
                order.order_volume = shares

            self.cash -= cost
            total_cost = self.avg_cost * self.position + exec_price * shares
            self.position += shares
            self.avg_cost = total_cost / self.position if self.position > 0 else 0

            order.traded_volume = shares
            order.traded_price = exec_price
            self._filled_orders[order_id] = order
            self.last_order_id = order_id

            self.trade_log.append({
                'time': str(MockDatetime._current),
                'action': 'BUY', 'price': exec_price,
                'shares': shares, 'cost': cost,
                'cash_after': self.cash, 'position_after': self.position,
            })
            return order_id

        elif shares < 0:  # Sell
            sell_shares = abs(shares)
            if self.position < sell_shares:
                sell_shares = self.position
                order.order_volume = sell_shares
            if sell_shares <= 0:
                return None

            proceeds = exec_price * sell_shares * (1 - 0.00025 - 0.001)
            self.cash += proceeds
            self.position -= sell_shares

            order.traded_volume = sell_shares
            order.traded_price = exec_price
            self._filled_orders[order_id] = order
            self.last_order_id = order_id

            self.trade_log.append({
                'time': str(MockDatetime._current),
                'action': 'SELL', 'price': exec_price,
                'shares': sell_shares, 'proceeds': proceeds,
                'cash_after': self.cash, 'position_after': self.position,
            })
            return order_id

        return None

    def cancel_order(self, order_id):
        return False

    def refresh_daily_cache(self):
        """Refresh daily data cache - no-op for mock."""
        pass

    def disconnect(self):
        pass


# ============================================================
# Mock ContextInfo
# ============================================================

class MockContextInfo:
    def __init__(self, conn):
        self.conn = conn
        self.st = {}
        self._stock_code = conn.code

    def get_history_data(self, *a, **kw):
        return self.conn.get_history_data(*a, **kw)

    def get_full_tick(self, codes):
        return self.conn.get_full_tick(codes)

    def is_last_bar(self):
        return True

    def run_time(self, *a):
        pass

    def get_stock_name(self):
        return '长飞光纤'


# ============================================================
# Main backtest runner for v39
# ============================================================

class BacktestTerminated(Exception):
    """Raised to break out of the strategy's while loop."""
    pass


def run_backtest_v39(code='601869.SH', initial_cash=100000.0, initial_position=200,
                     date_str='20260910', verbose=True):
    """Run 1-minute backtest for DayTradeing_v39 strategy.

    v39 uses a while-loop. We run it in a thread and inject bars via _time.sleep patching.
    """
    print(f'=== 1-Minute Backtest (v39): {code} ===')
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
    target_date = date_str
    try:
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
        print('ERROR: No minute bars available')
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
    mods_to_remove = [k for k in sys.modules
                      if 'DayTradeing_v39' in k or k == 'DayTradeing_v39_stragety_miniqmt']
    for mod in mods_to_remove:
        del sys.modules[mod]

    strategy_path = os.path.join(_DAYT_ROOT, 'DayTradeing_v39_stragety_miniqmt.py')
    import importlib.util
    spec = importlib.util.spec_from_file_location('DayTradeing_v39_stragety_miniqmt', strategy_path)
    strategy_mod = importlib.util.module_from_spec(spec)
    sys.modules['DayTradeing_v39_stragety_miniqmt'] = strategy_mod
    spec.loader.exec_module(strategy_mod)

    # ── Step 5: Initialize strategy ──
    print('[5] Initializing strategy...')
    runner = strategy_mod.StrategyRunner(dry_run=True)

    # ── Step 6: Run with _time.sleep patching ──
    print(f'\n[6] Running backtest ({len(minute_df)} minute bars)...')
    print('=' * 70)

    total_bars = len(minute_df)
    bar_idx = [0]  # mutable counter for closure
    equity_curve = []
    bar_times = []
    state_transitions = []
    prev_state = [None]

    # Track state before strategy runs
    mock_conn.set_tick_index(0)
    initial_price = float(minute_df.iloc[0]['close'])

    # Patch _time.sleep to inject minute bars
    original_sleep = _real_time_module.sleep

    def mock_sleep(secs):
        """Intercept sleep calls to advance minute bars."""
        if bar_idx[0] >= total_bars:
            # All bars consumed, terminate strategy
            raise BacktestTerminated('All bars consumed')

        # Advance to next bar
        idx = bar_idx[0]
        mock_conn.set_tick_index(idx)

        # Update mock time
        try:
            row = minute_df.iloc[idx]
            bar_time = pd.to_datetime(row['time'])
            MockDatetime._current = bar_time.to_pydatetime()
            MockTime._offset = bar_time.timestamp() - MockTime._base_ts
        except Exception:
            pass

        # Track equity
        price = float(minute_df.iloc[idx]['close'])
        total_value = mock_conn.cash + mock_conn.position * price
        equity_curve.append(total_value)
        bar_times.append(str(MockDatetime._current))

        # Track state transitions
        cur_state = runner.st.get('fstate', 'IDLE')
        if cur_state != prev_state[0]:
            state_transitions.append({
                'time': str(MockDatetime._current),
                'from': prev_state[0] or 'START',
                'to': cur_state,
                'price': price,
            })
            prev_state[0] = cur_state

        bar_idx[0] += 1

        if (idx + 1) % 60 == 0 or idx == total_bars - 1:
            print(f'  [{idx+1}/{total_bars}] {MockDatetime._current.strftime("%H:%M")} '
                  f'price={price:.2f} state={cur_state} '
                  f'cash={mock_conn.cash:,.0f} pos={mock_conn.position} '
                  f'equity={total_value:,.0f}')

        # Don't actually sleep - just advance
        return

    # Run strategy in a thread
    strategy_error = [None]
    strategy_thread = [None]

    def run_strategy():
        try:
            runner.run()
        except BacktestTerminated:
            pass  # Expected termination
        except Exception as e:
            strategy_error[0] = e
            import traceback
            traceback.print_exc()

    # Patch _time.sleep globally and in the strategy module
    _time.sleep = mock_sleep
    strategy_mod._time.sleep = mock_sleep

    # Also patch in core.config
    import core.config as cfg_mod
    cfg_mod.datetime = MockDatetime
    cfg_mod._time = MockTime

    # Patch in connector module
    from Stragety.MiniQMT_Stragety.DayT.infra import connector as conn_mod
    conn_mod._time = MockTime

    # Start strategy thread
    strategy_thread[0] = threading.Thread(target=run_strategy, daemon=True)
    strategy_thread[0].start()

    # Wait for completion
    strategy_thread[0].timeout = 30  # 30 second timeout
    strategy_thread[0].join(timeout=30)

    # Restore original sleep
    _real_time_module.sleep = original_sleep

    if strategy_thread[0].is_alive():
        print('\n  WARNING: Strategy thread timed out after 30s')

    if strategy_error[0]:
        print(f'\n  Strategy error: {strategy_error[0]}')

    # ── Step 7: End-of-day settlement ──
    print('\n[7] End-of-day settlement...')
    final_price = float(minute_df.iloc[-1]['close'])
    if mock_conn.position > 0:
        print(f'  Closing {mock_conn.position} shares at {final_price:.2f}')
        mock_conn.order_stock(code, -mock_conn.position, 'FIX', final_price)

    # ── Step 8: Results ──
    print('\n' + '=' * 70)
    print('=== BACKTEST RESULTS (v39) ===')
    print('=' * 70)

    final_equity = mock_conn.cash
    initial_equity = initial_cash + initial_position * initial_price
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

    return {
        'date': date_str,
        'initial_equity': initial_equity,
        'final_equity': final_equity,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'trades': mock_conn.trade_log,
        'state_transitions': state_transitions,
        'equity_curve': equity_curve,
        'bar_times': bar_times,
        'final_cash': mock_conn.cash,
        'final_position': mock_conn.position,
        'final_price': float(minute_df.iloc[-1]['close']),
        'initial_price': initial_price,
    }


def _setup_mocks(mock_conn):
    """Set up all module-level mocks."""
    import core.config as cfg_mod
    cfg_mod.datetime = MockDatetime

    from Stragety.MiniQMT_Stragety.DayT.infra import connector as conn_mod
    conn_mod._time = MockTime
    conn_mod._global_conn = mock_conn
    conn_mod._global_dry_run = True

    def mock_get_trade_detail(account_id, account_type, data_type):
        if data_type.upper() == 'POSITION':
            return mock_conn.query_positions()
        elif data_type.upper() == 'ACCOUNT':
            return [mock_conn.query_account()]
        return []

    def mock_order_shares(stockcode, shares, style='LATEST', price=None, ContextInfo=None, accId=None):
        _price = price
        if price is not None and not isinstance(price, (int, float)):
            _price = None
        return mock_conn.order_stock(stockcode, shares, style, _price)

    conn_mod.get_trade_detail_data = mock_get_trade_detail
    conn_mod.order_shares = mock_order_shares

    # Patch MiniQMTConnector class
    class PatchedMiniQMTConnector:
        def __init__(self, *args, **kwargs):
            self.trader = mock_conn.trader
            self._account_obj = mock_conn._account_obj
            self.last_order_id = None

        def __getattr__(self, name):
            return getattr(mock_conn, name)

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

        def refresh_daily_cache(self):
            pass

        def disconnect(self):
            pass

        def cancel_order(self, *args):
            return False

    conn_mod.MiniQMTConnector = PatchedMiniQMTConnector

    # Patch MockContextInfo
    conn_mod.MockContextInfo = MockContextInfo

    # Patch time in all modules
    for mod_name, mod in sys.modules.items():
        if hasattr(mod, '_time') and hasattr(mod._time, 'time'):
            mod._time = MockTime


# ============================================================
# Multi-day backtest
# ============================================================

def run_multi_day_v39(code='601869.SH', initial_cash=100000.0, initial_position=200,
                      dates=None, verbose=True):
    """Run multi-day 1-minute backtest for DayTradeing_v39.

    Each day resets the strategy (like production), carrying forward cash and position.
    """
    if dates is None:
        dates = ['20260905', '20260906', '20260908', '20260909', '20260910']

    print(f'=== Multi-Day v39 Backtest: {code} ===')
    print(f'Dates: {dates}')
    print(f'Initial: cash={initial_cash:,.0f} position={initial_position} shares')
    print('=' * 70)

    all_results = []
    cur_cash = initial_cash
    cur_pos = initial_position

    for date_str in dates:
        print(f'\n{"="*70}')
        print(f'  DAY: {date_str}')
        print(f'{"="*70}')

        result = run_backtest_v39(
            code=code,
            initial_cash=cur_cash,
            initial_position=cur_pos,
            date_str=date_str,
            verbose=verbose,
        )

        if result:
            all_results.append(result)
            cur_cash = result['final_cash']
            cur_pos = result['final_position']

    # ── Aggregate results ──
    print('\n' + '=' * 70)
    print('=== MULTI-DAY SUMMARY (v39) ===')
    print('=' * 70)

    total_trades = 0
    total_pnl = 0
    for r in all_results:
        total_trades += len(r.get('trades', []))
        total_pnl += r.get('pnl', 0)
        print(f"  {r['date']}: P&L {r['pnl']:+,.0f} ({r['pnl_pct']:+.2f}%) "
              f"trades={len(r.get('trades', []))} "
              f"final_pos={r['final_position']}")

    final_equity = cur_cash + cur_pos * (all_results[-1]['final_price'] if all_results else 0)
    initial_equity = initial_cash + initial_position * (all_results[0]['initial_price'] if all_results else 0)
    overall_pnl = final_equity - initial_equity
    overall_pct = overall_pnl / initial_equity * 100 if initial_equity > 0 else 0

    print(f'\nFinal: cash={cur_cash:,.0f} position={cur_pos}')
    print(f'Final equity: {final_equity:,.0f}')
    print(f'Overall P&L: {overall_pnl:+,.0f} ({overall_pct:+.2f}%)')
    print(f'Total trades: {total_trades}')

    return all_results


# ============================================================
# CLI entry point
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='1-minute backtest for DayTradeing_v39')
    parser.add_argument('--code', default='601869.SH', help='Stock code')
    parser.add_argument('--cash', type=float, default=100000, help='Initial cash')
    parser.add_argument('--position', type=int, default=200, help='Initial position')
    parser.add_argument('--date', default='20260910', help='Trading date (YYYYMMDD)')
    parser.add_argument('--multi-day', action='store_true',
                        help='Run multi-day backtest across multiple dates')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')

    if args.multi_day:
        results = run_multi_day_v39(
            code=args.code,
            initial_cash=args.cash,
            initial_position=args.position,
        )
    else:
        results = run_backtest_v39(
            code=args.code,
            initial_cash=args.cash,
            initial_position=args.position,
            date_str=args.date,
        )
