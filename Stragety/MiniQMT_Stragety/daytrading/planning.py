"""Build a daily trading plan from completed bars and a portfolio snapshot."""
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from core.indicators import daily_range_ma
from core.signals import compute_signal


@dataclass(frozen=True)
class DailyPlan:
    signal: dict
    completed_closes: tuple
    reconciliation_reason: str = ''


class DailyPlanBuilder:
    def __init__(self, settings, history_length=80):
        self.settings = settings
        self.history_length = int(history_length)

    def build(self, bars, tick, portfolio, trade_date=None):
        if bars is None or len(bars) < 60:
            raise ValueError('at least 60 daily bars are required')
        required = ('open', 'high', 'low', 'close', 'volume')
        missing = [name for name in required if name not in bars.columns]
        if missing:
            raise ValueError('daily bars missing fields: {}'.format(', '.join(missing)))

        today = pd.Timestamp(trade_date or datetime.now().date())
        dates = self._index_dates(bars.index)
        valid_dates = ~pd.isna(dates)
        warning = ''
        if valid_dates.any():
            completed = bars.loc[(dates < today) & valid_dates]
        else:
            completed = bars
            warning = 'daily bar index is unrecognizable; current-bar exclusion is unconfirmed'
        if len(completed) < 60:
            raise ValueError('fewer than 60 completed daily bars')

        opens = completed['open'].astype(float).tolist()
        highs = completed['high'].astype(float).tolist()
        lows = completed['low'].astype(float).tolist()
        closes = completed['close'].astype(float).tolist()
        volumes = completed['volume'].astype(float).tolist()
        last_close = float(tick.get('lastClose', 0) or 0)
        today_open = float(tick.get('open', 0) or last_close or 0)
        signal = compute_signal(
            opens, highs, lows, closes, volumes,
            yesterday_close=last_close)
        if signal is None:
            raise ValueError('daily signal could not be computed')

        if today_open > 0:
            range_pct = daily_range_ma(highs, lows, opens, 10)[-1]
            raw = today_open * (
                1.0 + signal['atr_pct'] * signal['sell_mult'] *
                self.settings.sell_trigger_scale)
            cap = today_open * (
                1.0 + range_pct * self.settings.daily_range_cap_mult)
            signal['sell_trigger_raw'] = round(raw, 2)
            signal['range_capped'] = bool(
                self.settings.daily_range_cap_enabled and raw > cap)
            signal['sell_trigger'] = round(
                min(raw, cap) if self.settings.daily_range_cap_enabled else raw, 2)
            signal['open_price'] = today_open
        if last_close > 0:
            signal['close_yday'] = last_close

        current = float(tick.get('lastPrice', 0) or today_open or
                        signal.get('open_price', 0))
        min_lots = 1
        short_lots = min(
            int(portfolio.sellable) // self.settings.lot_size,
            self.settings.max_daily_trades)
        cash_lots = int(
            portfolio.cash / (current * self.settings.lot_size * 1.01)
        ) if current > 0 else 0
        sellable_lots = int(portfolio.sellable) // self.settings.lot_size
        long_lots = min(
            cash_lots, sellable_lots, self.settings.max_daily_trades)
        do_short = bool(signal.get('do_short') and short_lots >= min_lots)
        do_long = bool(long_lots >= min_lots)
        signal['do_short'] = do_short
        signal['do_long'] = do_long
        signal['short_lots'] = short_lots
        signal['long_lots'] = long_lots
        if not do_short:
            signal['short_reason'] = signal.get('blocked_reason') or 'sellable inventory insufficient'
        floor = round(
            signal['open_price'] * (1.0 - self.settings.buy_trigger_pct), 2)
        trail = round(
            current * (1.0 - self.settings.buy_trigger_trail), 2)
        signal['buy_trigger_floor'] = floor
        signal['buy_trigger_trail'] = trail
        signal['buy_trigger'] = max(floor, trail)
        signal['sellback_target_hint'] = round(
            signal['buy_trigger'] *
            (1.0 + self.settings.sellback_rise_pct), 2)
        return DailyPlan(signal, tuple(self._clean_prices(closes)), warning)

    @staticmethod
    def _clean_prices(values):
        result = []
        for value in values:
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(price) and price > 0:
                result.append(price)
        return result

    @staticmethod
    def _index_dates(index):
        values = list(index)
        if not values:
            return pd.DatetimeIndex([])
        first = values[0]
        try:
            if isinstance(first, (int, np.integer)):
                digits = len(str(abs(int(first))))
                if digits >= 13:
                    return pd.to_datetime(values, unit='ms', errors='coerce')
                if digits == 10:
                    return pd.to_datetime(values, unit='s', errors='coerce')
                return pd.to_datetime(
                    [str(value) for value in values],
                    format='%Y%m%d', errors='coerce')
            return pd.to_datetime(values, errors='coerce')
        except Exception:
            return pd.DatetimeIndex([pd.NaT] * len(values))
