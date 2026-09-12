# -*- coding: utf-8 -*-
"""Small, side-effect-free probes for the MiniQMT runtime connections."""
import math


def quote_freshness(quote, now, market_open, stale_after=90, future_tolerance=5):
    """Classify source quote time, never the RPC/cache receipt time."""
    result = dict(status='NOT_APPLICABLE', quote_time=None, age=None, reason='non-trading session')
    if not market_open:
        return result
    quote = quote or {}
    try:
        price = float(quote.get('lastPrice', 0) or 0)
    except (TypeError, ValueError):
        price = 0
    if not math.isfinite(price) or price <= 0:
        return dict(result, status='UNAVAILABLE', reason='no valid lastPrice')
    stamp = quote.get('time')
    try:
        if isinstance(stamp, bool):
            raise ValueError('boolean time')
        stamp = float(stamp)
        # Accept contemporary Unix seconds/milliseconds, not calendar digits.
        if 1e12 <= stamp < 1e13:
            stamp /= 1000
        elif not 1e9 <= stamp < 1e10:
            raise ValueError('unsupported time')
    except (TypeError, ValueError, OverflowError):
        return dict(result, status='UNKNOWN', reason='missing or unsupported source time')
    age = now - stamp
    status = 'CLOCK_SKEW' if age < -future_tolerance else 'STALE' if age > stale_after else 'FRESH'
    reason = {'CLOCK_SKEW': 'source time ahead of local clock',
              'STALE': 'source quote old; not necessarily network failure',
              'FRESH': 'source quote within age limit'}[status]
    return dict(status=status, quote_time=stamp, age=age, reason=reason)


def probe_connections(conn, context, stock_qmt, check_trade, snapshot=None):
    """Return ``(healthy, detail)`` without reconnecting or placing an order."""
    problems = []

    if not getattr(conn, 'data_connected', False):
        problems.append('market connection flag is false')
    try:
        tick = context.get_full_tick([stock_qmt]) or {}
        quote = tick.get(stock_qmt) or {}
        if snapshot is not None:
            snapshot.update(quote=quote, received=True)
        last_price = float(quote.get('lastPrice', 0) or 0)
        last_close = float(quote.get('lastClose', 0) or 0)
        if last_price <= 0 and last_close <= 0:
            problems.append('market quote is unavailable')
    except Exception as error:
        problems.append('market quote error: {}'.format(error))

    if check_trade:
        if not getattr(conn, 'trade_connected', False):
            problems.append('trade connection flag is false')
        try:
            if conn.query_account() is None:
                problems.append('trade account query returned empty')
        except Exception as error:
            problems.append('trade account query error: {}'.format(error))

    return not problems, '; '.join(problems) if problems else 'market/trade probes OK'
