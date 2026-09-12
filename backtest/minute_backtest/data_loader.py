# -*- coding: utf-8 -*-
"""
Minute K-line data loader for backtesting.

Fetches 1-minute OHLCV data via mootdx (Tongdaxin TCP protocol).
Falls back to akshare if mootdx is unavailable.
"""
import os
import logging
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), '.cache')


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


# ============================================================
# mootdx loader (preferred — no IP blocking)
# ============================================================

def _fetch_mootdx(code_6, days=10):
    """Fetch 1-minute bars via mootdx. code_6='601869' (6-digit)."""
    from mootdx.quotes import Quotes

    client = Quotes.factory(market='std')
    # period=1 → 1-minute bars; offset max ~800 per call
    all_bars = []
    for offset in range(0, days * 240, 800):
        try:
            bars = client.bars(symbol=code_6, frequency=1, offset=offset)
            if bars is None or len(bars) == 0:
                break
            all_bars.append(bars)
        except Exception:
            break
    client.disconnect()

    if not all_bars:
        return None

    df = pd.concat(all_bars[::-1], ignore_index=True)
    # mootdx columns: open, close, high, low, volume, amount, datetime
    df = df.rename(columns={'datetime': 'time'})
    # Convert time to string format
    if 'time' in df.columns:
        df['time'] = df['time'].astype(str)
    return df


def _fetch_mootdx_daily(code_6, days=400):
    """Fetch daily bars via mootdx for signal computation."""
    from mootdx.quotes import Quotes

    client = Quotes.factory(market='std')
    all_bars = []
    for offset in range(0, days, 800):
        try:
            bars = client.bars(symbol=code_6, frequency=9, offset=offset)  # 9 = daily
            if bars is None or len(bars) == 0:
                break
            all_bars.append(bars)
        except Exception:
            break
    client.disconnect()

    if not all_bars:
        return None

    df = pd.concat(all_bars[::-1], ignore_index=True)
    if 'datetime' in df.columns:
        df['time'] = df['datetime'].astype(str)
    return df


# ============================================================
# akshare fallback
# ============================================================

def _fetch_akshare_minute(code_6):
    """Fetch 1-minute bars via akshare."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_minute(symbol='sh' + code_6, period='1')
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={'day': 'time', 'open': 'open', 'close': 'close',
                                'high': 'high', 'low': 'low', 'volume': 'volume'})
        return df
    except Exception as e:
        logger.warning('akshare minute fetch failed: %s', e)
        return None


def _fetch_akshare_daily(code_6, days=400):
    """Fetch daily bars via akshare (baidu source, works through proxy)."""
    try:
        import akshare as ak
        # Use baidu source (stock_zh_a_daily) which works through proxy
        df = ak.stock_zh_a_daily(symbol='sh' + code_6, adjust='qfq')
        if df is None or len(df) == 60:
            return None
        # baidu columns: date, open, high, low, close, volume, amount, ...
        df = df.rename(columns={'date': 'time'})
        # Keep only last 'days' rows
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning('akshare daily fetch failed: %s', e)
        return None


# ============================================================
# Public API
# ============================================================

def load_minute_data(code='601869.SH', days=10):
    """Load 1-minute K-line data for backtesting.

    Args:
        code: QMT-format code (e.g. '601869.SH')
        days: number of trading days to load

    Returns:
        DataFrame with columns: time, open, high, low, close, volume
    """
    code_6 = code.split('.')[0]
    _ensure_cache_dir()
    cache_file = os.path.join(CACHE_DIR, f'{code_6}_1min.parquet')

    # Try cache first (today's date)
    today = datetime.now().strftime('%Y%m%d')
    if os.path.exists(cache_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if mtime.strftime('%Y%m%d') == today:
            try:
                df = pd.read_parquet(cache_file)
                logger.info('Loaded %d minute bars from cache', len(df))
                return df
            except Exception:
                pass

    # Fetch fresh data
    df = None
    try:
        df = _fetch_mootdx(code_6, days=days)
        if df is not None:
            logger.info('Fetched %d minute bars via mootdx', len(df))
    except Exception as e:
        logger.info('mootdx unavailable: %s', e)

    if df is None:
        df = _fetch_akshare_minute(code_6)
        if df is not None:
            logger.info('Fetched %d minute bars via akshare', len(df))

    if df is not None and len(df) > 0:
        try:
            df.to_parquet(cache_file, index=False)
        except Exception:
            pass

    return df


def load_daily_data(code='601869.SH', days=400):
    """Load daily OHLCV data for signal computation.

    Returns:
        DataFrame with columns: time, open, high, low, close, volume
    """
    code_6 = code.split('.')[0]
    _ensure_cache_dir()
    cache_file = os.path.join(CACHE_DIR, f'{code_6}_daily.parquet')

    today = datetime.now().strftime('%Y%m%d')
    if os.path.exists(cache_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if mtime.strftime('%Y%m%d') == today:
            try:
                df = pd.read_parquet(cache_file)
                logger.info('Loaded %d daily bars from cache', len(df))
                return df
            except Exception:
                pass

    df = None
    # Try akshare first (baidu source works through proxy)
    try:
        df = _fetch_akshare_daily(code_6, days=days)
        if df is not None:
            logger.info('Fetched %d daily bars via akshare (baidu)', len(df))
    except Exception as e:
        logger.info('akshare daily failed: %s', e)

    if df is None:
        try:
            df = _fetch_mootdx_daily(code_6, days=days)
            if df is not None:
                logger.info('Fetched %d daily bars via mootdx', len(df))
        except Exception as e:
            logger.info('mootdx daily unavailable: %s', e)

    if df is not None and len(df) > 0:
        try:
            df.to_parquet(cache_file, index=False)
        except Exception:
            pass

    return df
