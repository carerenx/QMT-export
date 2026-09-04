# -*- coding: gbk -*-

"""
CSI 500 MA5/MA20 stock screener for QMT.

Run this strategy on a daily chart. It prints stocks satisfying both:
1. current price < MA5 * 0.96
2. current price > MA20

This strategy only screens and prints; it never submits orders.
"""

INDEX_CODE = '000905.SH'
SHORT_MA_DAYS = 5
LONG_MA_DAYS = 20
BELOW_MA5_RATE = 0.04


def _log(message):
    print(message)


def init(ContextInfo):
    stocks = ContextInfo.get_sector(INDEX_CODE)
    ContextInfo.set_universe(stocks)
    _log('[init] CSI 500 universe loaded: %d stocks' % len(stocks))


def handlebar(ContextInfo):
    if not ContextInfo.is_last_bar():
        return

    close_history = ContextInfo.get_history_data(
        LONG_MA_DAYS, '1d', 'close'
    )
    selected = []

    for code, closes in close_history.items():
        if len(closes) < LONG_MA_DAYS:
            continue

        prices = [float(value) for value in closes if value is not None]
        if len(prices) < LONG_MA_DAYS or any(value <= 0 for value in prices):
            continue

        current_price = prices[-1]
        ma5 = sum(prices[-SHORT_MA_DAYS:]) / float(SHORT_MA_DAYS)
        ma20 = sum(prices[-LONG_MA_DAYS:]) / float(LONG_MA_DAYS)

        if current_price < ma5 * (1.0 - BELOW_MA5_RATE) and current_price > ma20:
            deviation = (current_price / ma5 - 1.0) * 100.0
            selected.append((deviation, code, current_price, ma5, ma20))

    selected.sort(key=lambda item: item[0])

    _log('=' * 72)
    _log('CSI 500 screening result: price < MA5 * 96% and price > MA20')
    _log('code          price       MA5        MA20       vs_MA5')
    for deviation, code, current_price, ma5, ma20 in selected:
        _log('%-12s %10.2f %10.2f %10.2f %9.2f%%' % (
            code, current_price, ma5, ma20, deviation
        ))
    _log('matched: %d stocks' % len(selected))
    _log('=' * 72)
