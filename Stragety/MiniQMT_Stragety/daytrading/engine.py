"""Pure event-driven intraday decision engine.

The engine never imports QMT, reads the clock, sleeps, writes files, or submits an
order. Its Interface is: begin/restore a session, feed ticks, apply executions, and
take a serializable snapshot.
"""
from collections import deque
from dataclasses import asdict, dataclass, field, fields

from .domain import OrderEffect, OrderIntent, OrderStatus, remaining_legs


IDLE = 'IDLE'
SPIKING = 'SPIKING'
SOLD = 'SOLD'
DIPPING = 'DIPPING'
DONE = 'DONE'
FORCED = 'FORCED'
BT_DIPPING = 'BT_DIPPING'
BT_BOUGHT = 'BT_BOUGHT'
BT_SPIKING = 'BT_SPIKING'


@dataclass
class TradingSession:
    trade_date: str = ''
    initialized: bool = False
    daily_signal: dict = field(default_factory=dict)
    do_short: bool = False
    do_long: bool = False
    fstate: str = IDLE
    peak_price: float = 0.0
    dip_price: float = 0.0
    sell_fill_price: float = 0.0
    buyback_target: float = 0.0
    buyback_target_pct: float = 0.0
    short_legs: list = field(default_factory=list)
    long_legs: list = field(default_factory=list)
    ladder_sell_target: float = 0.0
    ladder_buy_target: float = 0.0
    ladder_sold_count: int = 0
    ladder_bought_count: int = 0
    trade_count_short: int = 0
    trade_count_long: int = 0
    bt_dip_price: float = 0.0
    bt_buy_trigger: float = 0.0
    bt_buy_fill_price: float = 0.0
    bt_sellback_target: float = 0.0
    bt_sell_peak_price: float = 0.0
    state_enter_time: str = ''
    sell_elapsed_bars: int = 0
    day_pnl: float = 0.0
    total_pnl: float = 0.0
    total_t_days: int = 0
    stop_loss_hit: bool = False
    stop_loss_retry_at: float = 0.0
    locked: bool = False
    lock_reason: str = ''
    lock_since: str = ''
    lock_cooldown_until: float = 0.0
    price_history: deque = field(default_factory=deque)
    mom_state: str = 'MOM_IDLE'
    mom_peak: float = 0.0
    mom_dip: float = 0.0
    mom_sell_price: float = 0.0
    mom_buy_price: float = 0.0
    mom_leg_shares: int = 0
    mom_trade_count: int = 0
    mom_price_history: deque = field(default_factory=deque)
    mom_atr_history: deque = field(default_factory=deque)
    mom_trigger_pct: float = 0.0
    base_shares: int = 0
    base_can_use: int = 0
    base_cost: float = 0.0
    avail_cash: float = 0.0
    reconcile_required: bool = False
    reconcile_reason: str = ''
    last_order_id: object = None
    last_order_label: str = ''
    ma_completed_closes: list = field(default_factory=list)

    def snapshot(self):
        value = asdict(self)
        value['price_history'] = list(self.price_history)
        value['mom_price_history'] = list(self.mom_price_history)
        value['mom_atr_history'] = list(self.mom_atr_history)
        return value

    @classmethod
    def restore(cls, value):
        names = {item.name for item in fields(cls)}
        kwargs = {key: item for key, item in value.items() if key in names}
        for key in ('price_history', 'mom_price_history', 'mom_atr_history'):
            kwargs[key] = deque(tuple(item) for item in kwargs.get(key, []))
        for key in ('short_legs', 'long_legs'):
            kwargs[key] = [tuple(item) for item in kwargs.get(key, [])]
        return cls(**kwargs)


class DayTradingEngine:
    """Owns all primary and MOM state transitions behind a small Interface."""

    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or TradingSession()

    def begin_session(self, trade_date, signal, portfolio, completed_closes=()):
        previous_total_pnl = self.session.total_pnl
        previous_total_days = self.session.total_t_days
        self.session = TradingSession(
            trade_date=trade_date,
            initialized=True,
            daily_signal=dict(signal),
            do_short=bool(signal.get('do_short')),
            do_long=bool(signal.get('do_long')),
            state_enter_time='',
            total_pnl=previous_total_pnl,
            total_t_days=previous_total_days,
            base_shares=int(portfolio.shares),
            base_can_use=int(portfolio.sellable),
            base_cost=float(portfolio.cost),
            avail_cash=float(portfolio.cash),
            ma_completed_closes=list(completed_closes),
        )

    def update_plan(self, signal, portfolio, completed_closes=()):
        st = self.session
        st.daily_signal = dict(signal)
        st.do_short = bool(signal.get('do_short'))
        st.do_long = bool(signal.get('do_long'))
        st.ma_completed_closes = list(completed_closes)
        self.update_portfolio(portfolio)
        st.initialized = True

    def update_portfolio(self, portfolio):
        st = self.session
        if portfolio.valid:
            st.base_shares = int(portfolio.shares)
            st.base_can_use = int(portfolio.sellable)
            st.base_cost = float(portfolio.cost)
            st.avail_cash = float(portfolio.cash)

    def restore(self, value):
        self.session = TradingSession.restore(value)
        if self.has_open_legs:
            self.session.reconcile_required = True
            self.session.reconcile_reason = (
                'open intraday leg restored after process restart')

    def snapshot(self):
        return self.session.snapshot()

    @property
    def has_open_legs(self):
        st = self.session
        return bool(st.short_legs or st.long_legs or st.mom_leg_shares)

    @property
    def reserved_sellable_shares(self):
        st = self.session
        reserved = self._leg_shares(st.long_legs)
        if st.mom_state in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
            reserved += st.mom_leg_shares
        return reserved

    def on_tick(self, price, now_ts, now_hms):
        if price <= 0 or not self.session.initialized:
            return []
        self._assess_strength(price, now_ts, now_hms)
        actions = []
        if now_hms >= self.settings.force_close_time:
            actions.extend(self._force_close_actions(price))
            return actions

        if self.settings.mom_enabled:
            mom_action = self._mom_tick(price, now_ts)
            if mom_action is not None:
                actions.append(mom_action)

        action = self._primary_tick(price, now_ts, now_hms)
        if action is not None:
            actions.append(action)
        if self.session.fstate in (SOLD, DIPPING):
            self.session.sell_elapsed_bars += 1
        return actions

    def apply_execution(self, intent, result):
        st = self.session
        st.last_order_id = result.order_id
        st.last_order_label = intent.label
        if result.reconciliation_reason:
            st.reconcile_required = True
            st.reconcile_reason = result.reconciliation_reason
        filled = int(result.signed_filled_shares)
        fill_price = float(result.average_fill_price or intent.reference_price)
        label = intent.label
        if label == 'REV-T sell':
            self._apply_rev_sell(intent, result, filled, fill_price)
        elif label.startswith('REV-T buyback') or label == 'REV-T force buyback':
            self._apply_rev_buyback(intent, filled, fill_price)
        elif label == 'FWD-T buy':
            self._apply_fwd_buy(intent, result, filled, fill_price)
        elif label in ('FWD-T sell', 'FWD-T force sell'):
            self._apply_fwd_sell(
                intent, filled, label.endswith('force sell'), fill_price)
        elif label == 'MOM short':
            self._apply_mom_short(intent, filled, fill_price)
        elif label in ('MOM buyback', 'MOM emrg buyback', 'MOM force buyback'):
            self._apply_mom_buyback(intent, filled, fill_price)
        elif label == 'MOM long':
            self._apply_mom_long(intent, filled, fill_price)
        elif label in ('MOM sellback', 'MOM stop-loss', 'MOM force sell'):
            self._apply_mom_sell(intent, filled, fill_price)
        self._clear_reconciliation_if_flat()

    def _primary_tick(self, price, now_ts, now_hms):
        st = self.session
        state = st.fstate
        if state == IDLE:
            signal = st.daily_signal
            if st.do_short and price >= signal.get('sell_trigger', float('inf')):
                if (st.base_can_use >= self.settings.min_lot and
                        st.trade_count_short < self.settings.max_daily_trades and
                        not st.locked):
                    st.trade_count_short += 1
                    st.fstate = SPIKING
                    st.peak_price = price
                    st.state_enter_time = now_hms
                    return None
            if st.do_long and price <= signal.get('buy_trigger', 0):
                if st.trade_count_long < self.settings.max_daily_trades:
                    st.trade_count_long += 1
                    st.fstate = BT_DIPPING
                    st.bt_dip_price = price
                    st.bt_buy_trigger = signal.get('buy_trigger', 0)
                    st.state_enter_time = now_hms
            return None
        if state == SPIKING:
            st.peak_price = max(st.peak_price, price)
            pullback = ((st.peak_price - price) / st.peak_price
                        if st.peak_price > 0 else 0)
            if pullback >= self.settings.pullback_pct:
                atr_pct = st.daily_signal.get('atr_pct', 0.03)
                pct = atr_pct * self.settings.buyback_trigger_mult
                st.buyback_target = round(price * (1.0 - pct), 2)
                st.buyback_target_pct = pct * 100
                st.sell_elapsed_bars = 0
                return OrderIntent(
                    OrderEffect.OPEN_SHORT, self.settings.lot_size,
                    price, 'REV-T sell')
        elif state == SOLD:
            ladder = st.ladder_sell_target
            if (ladder > 0 and price >= ladder and
                    st.trade_count_short < self.settings.max_daily_trades and
                    st.base_can_use >= self.settings.min_lot and not st.locked):
                st.trade_count_short += 1
                st.ladder_sold_count += 1
                st.fstate = SPIKING
                st.peak_price = price
                return None
            if (self.settings.emergency_buyback and st.sell_fill_price > 0 and
                    price >= st.sell_fill_price *
                    (1.0 + self.settings.emergency_buyback_pct)):
                return self._rev_buyback_intent(price, 'EMERG')
            target = st.buyback_target
            if (st.sell_elapsed_bars > 30 and st.sell_fill_price > 0 and
                    price > st.sell_fill_price * 0.995):
                tightened = st.sell_fill_price * (
                    1.0 - st.daily_signal.get('atr_pct', 0.03) *
                    self.settings.buyback_trigger_mult *
                    self.settings.buyback_tighten_mult)
                target = round(max(target, tightened), 2)
            if target > 0 and price <= target:
                st.fstate = DIPPING
                st.dip_price = price
        elif state == DIPPING:
            st.dip_price = min(st.dip_price or price, price)
            if ((price - st.dip_price) / st.dip_price >= self.settings.bounce_pct):
                return self._rev_buyback_intent(price, 'NORMAL')
        elif state == BT_DIPPING:
            st.bt_dip_price = min(st.bt_dip_price or price, price)
            if ((price - st.bt_dip_price) / st.bt_dip_price >=
                    self.settings.bounce_pct):
                return OrderIntent(
                    OrderEffect.OPEN_LONG, self.settings.lot_size,
                    price, 'FWD-T buy')
        elif state == BT_BOUGHT:
            ladder = st.ladder_buy_target
            if (ladder > 0 and price <= ladder and
                    st.trade_count_long < self.settings.max_daily_trades):
                st.trade_count_long += 1
                st.ladder_bought_count += 1
                st.fstate = BT_DIPPING
                st.bt_dip_price = price
                return None
            average = self._leg_average(st.long_legs)
            if average > 0 and price <= average * (1.0 - self.settings.stop_loss_pct):
                return OrderIntent(
                    OrderEffect.CLOSE_LONG, self._leg_shares(st.long_legs),
                    price, 'FWD-T force sell')
            if st.bt_sellback_target > 0 and price >= st.bt_sellback_target:
                st.fstate = BT_SPIKING
                st.bt_sell_peak_price = price
        elif state == BT_SPIKING:
            st.bt_sell_peak_price = max(st.bt_sell_peak_price, price)
            pullback = ((st.bt_sell_peak_price - price) / st.bt_sell_peak_price
                        if st.bt_sell_peak_price > 0 else 0)
            if pullback >= self.settings.pullback_pct:
                return OrderIntent(
                    OrderEffect.CLOSE_LONG, self._leg_shares(st.long_legs),
                    price, 'FWD-T sell')

        if st.fstate in (SOLD, DIPPING) and st.short_legs:
            unrealized = self._short_gross(st.short_legs, price)
            notional = sum(entry * shares for entry, shares in st.short_legs)
            st.day_pnl = unrealized
            if (st.stop_loss_hit and now_ts >= st.stop_loss_retry_at or
                    unrealized < -notional * self.settings.stop_loss_pct):
                st.stop_loss_hit = True
                st.stop_loss_retry_at = now_ts + 5.0
                return self._rev_buyback_intent(price, 'FORCE')
        return None

    def _force_close_actions(self, price):
        st = self.session
        actions = []
        if st.short_legs:
            actions.append(self._rev_buyback_intent(price, 'FORCE'))
        elif st.long_legs:
            actions.append(OrderIntent(
                OrderEffect.CLOSE_LONG, self._leg_shares(st.long_legs),
                price, 'FWD-T force sell'))
        elif st.fstate not in (DONE, FORCED):
            st.fstate = DONE
        if self.settings.mom_enabled and st.mom_leg_shares > 0:
            if st.mom_state in ('MOM_SOLD', 'MOM_DIPPING'):
                actions.append(OrderIntent(
                    OrderEffect.CLOSE_SHORT, st.mom_leg_shares,
                    price, 'MOM force buyback'))
            elif st.mom_state in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING'):
                actions.append(OrderIntent(
                    OrderEffect.CLOSE_LONG, st.mom_leg_shares,
                    price, 'MOM force sell'))
        return actions

    def _rev_buyback_intent(self, price, reason):
        shares = self._leg_shares(self.session.short_legs)
        if shares <= 0:
            return None
        return OrderIntent(
            OrderEffect.CLOSE_SHORT, shares, price,
            'REV-T buyback({})'.format(reason))

    def _apply_rev_sell(self, intent, result, filled, fill_price):
        st = self.session
        sold = max(0, -filled)
        if sold <= 0:
            st.trade_count_short = max(0, st.trade_count_short - 1)
            st.fstate = IDLE
            return
        st.sell_fill_price = fill_price
        st.short_legs.append((fill_price, sold))
        st.ladder_sell_target = (
            round(fill_price *
                  (1.0 + self.settings.ladder_up_step_pct), 2)
            if result.status == OrderStatus.FILLED else 0.0)
        st.fstate = SOLD

    def _apply_rev_buyback(self, intent, filled, fill_price):
        st = self.session
        bought = max(0, filled)
        if bought <= 0:
            st.fstate = SOLD
            return
        closed_legs, remaining = self._consume_legs(st.short_legs, bought)
        st.total_pnl += sum(
            (entry - fill_price) * shares
            for entry, shares in closed_legs)
        st.short_legs = remaining
        if remaining:
            st.sell_fill_price = self._leg_average(remaining)
            st.fstate = SOLD
        else:
            st.ladder_sell_target = 0.0
            st.ladder_sold_count = 0
            st.total_t_days += 1
            if 'FORCE' in intent.label:
                st.fstate = FORCED
            else:
                self._resume_or_done()

    def _apply_fwd_buy(self, intent, result, filled, fill_price):
        st = self.session
        bought = max(0, filled)
        if bought <= 0:
            st.trade_count_long = max(0, st.trade_count_long - 1)
            st.fstate = BT_BOUGHT if st.long_legs else IDLE
            return
        st.long_legs.append((fill_price, bought))
        st.bt_buy_fill_price = self._leg_average(st.long_legs)
        st.bt_sellback_target = round(
            st.bt_buy_fill_price * (1.0 + self.settings.sellback_rise_pct), 2)
        st.ladder_buy_target = (
            round(fill_price *
                  (1.0 - self.settings.ladder_down_step_pct), 2)
            if result.status == OrderStatus.FILLED else 0.0)
        st.fstate = BT_BOUGHT

    def _apply_fwd_sell(self, intent, filled, forced, fill_price):
        st = self.session
        sold = max(0, -filled)
        if sold <= 0:
            st.fstate = BT_BOUGHT
            return
        closed_legs, remaining = self._consume_legs(st.long_legs, sold)
        st.total_pnl += sum(
            (fill_price - entry) * shares
            for entry, shares in closed_legs)
        st.long_legs = remaining
        if remaining:
            st.bt_buy_fill_price = self._leg_average(remaining)
            st.fstate = BT_BOUGHT
        else:
            st.ladder_buy_target = 0.0
            st.ladder_bought_count = 0
            st.total_t_days += 1
            if forced:
                st.fstate = FORCED
            else:
                self._resume_or_done()

    def _resume_or_done(self):
        st = self.session
        can_short = st.do_short and st.trade_count_short < self.settings.max_daily_trades
        can_long = st.do_long and st.trade_count_long < self.settings.max_daily_trades
        if can_short or can_long:
            if st.sell_fill_price > st.daily_signal.get('sell_trigger', 0):
                st.daily_signal['sell_trigger'] = st.sell_fill_price
            st.fstate = IDLE
            st.peak_price = st.dip_price = 0.0
            st.sell_fill_price = st.buyback_target = 0.0
            st.stop_loss_hit = False
        else:
            st.fstate = DONE

    def _mom_tick(self, price, now_ts):
        st = self.session
        self._append_window(st.mom_price_history, now_ts, price,
                            self.settings.mom_window_sec)
        self._append_window(st.mom_atr_history, now_ts, price,
                            self.settings.mom_atr_window_sec)
        atr = self._momentum_atr(st.mom_atr_history)
        base = st.mom_price_history[0][1] if st.mom_price_history else 0
        if base > 0:
            st.mom_trigger_pct = min(
                self.settings.mom_trigger_max_pct,
                max(self.settings.mom_trigger_min_pct,
                    atr / base * self.settings.mom_atr_mult))
        state = st.mom_state
        if state == 'MOM_IDLE' and st.mom_trade_count < self.settings.mom_max_daily_trades:
            direction = self._momentum_direction(price)
            if direction == 'UP':
                st.mom_state, st.mom_peak = 'MOM_SPIKING', price
            elif direction == 'DOWN':
                st.mom_state, st.mom_dip = 'MOM_BT_DIPPING', price
        elif state == 'MOM_SPIKING':
            st.mom_peak = max(st.mom_peak, price)
            if ((st.mom_peak - price) / st.mom_peak >= self.settings.pullback_pct):
                return OrderIntent(OrderEffect.OPEN_SHORT,
                                   self.settings.mom_lot_size, price, 'MOM short')
        elif state == 'MOM_SOLD':
            if (self.settings.mom_emergency_buyback and st.mom_sell_price > 0 and
                    price >= st.mom_sell_price *
                    (1.0 + self.settings.emergency_buyback_pct)):
                return OrderIntent(OrderEffect.CLOSE_SHORT,
                                   st.mom_leg_shares, price, 'MOM emrg buyback')
            if (st.mom_sell_price > 0 and price <= st.mom_sell_price *
                    (1.0 - self.settings.mom_short_buyback_pct)):
                st.mom_state, st.mom_dip = 'MOM_DIPPING', price
        elif state == 'MOM_DIPPING':
            st.mom_dip = min(st.mom_dip or price, price)
            if ((price - st.mom_dip) / st.mom_dip >= self.settings.bounce_pct):
                return OrderIntent(OrderEffect.CLOSE_SHORT,
                                   st.mom_leg_shares, price, 'MOM buyback')
        elif state == 'MOM_BT_DIPPING':
            st.mom_dip = min(st.mom_dip or price, price)
            if ((price - st.mom_dip) / st.mom_dip >= self.settings.bounce_pct):
                return OrderIntent(OrderEffect.OPEN_LONG,
                                   self.settings.mom_lot_size, price, 'MOM long')
        elif state == 'MOM_BT_BOUGHT':
            if price <= st.mom_buy_price * (1.0 - self.settings.stop_loss_pct):
                return OrderIntent(OrderEffect.CLOSE_LONG,
                                   st.mom_leg_shares, price, 'MOM stop-loss')
            if price >= st.mom_buy_price * (1.0 + self.settings.mom_long_sellback_pct):
                st.mom_state, st.mom_peak = 'MOM_BT_SPIKING', price
        elif state == 'MOM_BT_SPIKING':
            st.mom_peak = max(st.mom_peak, price)
            if ((st.mom_peak - price) / st.mom_peak >= self.settings.pullback_pct):
                return OrderIntent(OrderEffect.CLOSE_LONG,
                                   st.mom_leg_shares, price, 'MOM sellback')
        return None

    def _apply_mom_short(self, intent, filled, fill_price):
        sold = max(0, -filled)
        st = self.session
        if sold > 0:
            st.mom_sell_price = fill_price
            st.mom_leg_shares = sold
            st.mom_trade_count += 1
            st.mom_state = 'MOM_SOLD'
        else:
            st.mom_state = 'MOM_IDLE'

    def _apply_mom_buyback(self, intent, filled, fill_price):
        bought = max(0, filled)
        st = self.session
        if bought <= 0:
            return
        st.total_pnl += (st.mom_sell_price - fill_price) * bought
        st.mom_leg_shares = max(0, st.mom_leg_shares - bought)
        if st.mom_leg_shares:
            st.mom_state = 'MOM_SOLD'
        else:
            st.mom_state = 'MOM_IDLE'
            st.mom_sell_price = 0.0
            st.total_t_days += 1

    def _apply_mom_long(self, intent, filled, fill_price):
        bought = max(0, filled)
        st = self.session
        if bought > 0:
            st.mom_buy_price = fill_price
            st.mom_leg_shares = bought
            st.mom_trade_count += 1
            st.mom_state = 'MOM_BT_BOUGHT'
        else:
            st.mom_state = 'MOM_IDLE'

    def _apply_mom_sell(self, intent, filled, fill_price):
        sold = max(0, -filled)
        st = self.session
        if sold <= 0:
            return
        st.total_pnl += (fill_price - st.mom_buy_price) * sold
        st.mom_leg_shares = max(0, st.mom_leg_shares - sold)
        if st.mom_leg_shares:
            st.mom_state = 'MOM_BT_BOUGHT'
        else:
            st.mom_state = 'MOM_IDLE'
            st.mom_buy_price = 0.0
            st.total_t_days += 1

    def _assess_strength(self, price, now_ts, now_hms):
        st = self.session
        open_price = st.daily_signal.get('open_price', 0)
        if open_price <= 0:
            return
        self._append_window(st.price_history, now_ts, price,
                            self.settings.lock_lookback_sec)
        if len(st.price_history) < 10:
            return
        prices = [item[1] for item in st.price_history]
        first, current, high = prices[0], prices[-1], max(prices)
        should_lock = (
            current > open_price * (1.0 + self.settings.lock_price_ratio) and
            first > 0 and (current - first) / first > self.settings.lock_momentum_pct and
            high > 0 and (high - current) / high < self.settings.lock_drawdown_pct)
        if should_lock:
            st.locked = True
            st.lock_since = st.lock_since or now_hms
            st.lock_cooldown_until = max(
                st.lock_cooldown_until,
                now_ts + self.settings.lock_cooldown_sec)
        elif st.locked and now_ts >= st.lock_cooldown_until:
            st.locked = False
            st.lock_reason = st.lock_since = ''

    def _clear_reconciliation_if_flat(self):
        st = self.session
        if (not self.has_open_legs and
                st.reconcile_reason.startswith('open intraday leg')):
            st.reconcile_required = False
            st.reconcile_reason = ''

    def _momentum_direction(self, price):
        st = self.session
        if len(st.mom_price_history) < 2 or st.mom_trigger_pct <= 0:
            return None
        base = st.mom_price_history[0][1]
        change = (price - base) / base if base > 0 else 0
        if change >= st.mom_trigger_pct:
            return 'UP'
        if change <= -st.mom_trigger_pct:
            return 'DOWN'
        return None

    @staticmethod
    def _append_window(window, now_ts, price, seconds):
        window.append((now_ts, price))
        cutoff = now_ts - seconds
        while window and window[0][0] < cutoff:
            window.popleft()

    @staticmethod
    def _momentum_atr(history):
        buckets = {}
        for timestamp, price in history:
            bucket = int(timestamp // 60)
            high, low = buckets.get(bucket, (price, price))
            buckets[bucket] = (max(high, price), min(low, price))
        ranges = sorted(high - low for high, low in buckets.values())
        if not ranges:
            return 0.0
        middle = len(ranges) // 2
        if len(ranges) % 2:
            return ranges[middle]
        return (ranges[middle - 1] + ranges[middle]) / 2.0

    @staticmethod
    def _leg_shares(legs):
        return sum(int(shares) for _, shares in legs)

    @classmethod
    def _leg_average(cls, legs):
        shares = cls._leg_shares(legs)
        return (sum(float(price) * int(qty) for price, qty in legs) / shares
                if shares else 0.0)

    @staticmethod
    def _short_gross(legs, buyback_price):
        return sum((entry - buyback_price) * shares
                   for entry, shares in legs)

    @staticmethod
    def _consume_legs(legs, quantity):
        remaining_quantity = max(0, int(quantity))
        closed = []
        for entry, shares in legs:
            used = min(int(shares), remaining_quantity)
            if used:
                closed.append((float(entry), used))
            remaining_quantity -= used
            if remaining_quantity <= 0:
                break
        return closed, remaining_legs(legs, quantity)
