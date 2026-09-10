# -*- coding: utf-8 -*-
"""Small, side-effect-free probes for the MiniQMT runtime connections."""


def probe_connections(conn, context, stock_qmt, check_trade):
    """Return ``(healthy, detail)`` without reconnecting or placing an order."""
    problems = []

    if not getattr(conn, 'data_connected', False):
        problems.append('market connection flag is false')
    try:
        tick = context.get_full_tick([stock_qmt]) or {}
        quote = tick.get(stock_qmt) or {}
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
