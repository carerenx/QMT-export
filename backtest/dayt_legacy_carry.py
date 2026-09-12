"""Explicit, offline-only overnight semantics for the legacy strategy adapters.

This changes legacy behavior and MUST NOT be labelled an unmodified baseline.
No forced close, account reset or new cycle while carried legs remain.
"""
from copy import deepcopy


class LegacyCarry:
    def __init__(self, runner, broker, module, version):
        self.runner, self.broker, self.module, self.version = runner, broker, module, version
        self.active = False
        self.events = []
        self.daily_init = runner._daily_init
        self.block_reason = runner._new_leg_block_reason

    def has_legs(self):
        return any(self.broker.book.legs.values())

    def install(self, stack, patch):
        stack.enter_context(patch.object(self.runner, '_daily_init', self.initialize))
        stack.enter_context(patch.object(self.runner, '_new_leg_block_reason',
            lambda: 'BACKTEST CARRY: exits only until all carried legs close' if self.active else self.block_reason()))
        if self.version == 'v39' and hasattr(self.runner, '_mom_force_close'):
            force_close = self.runner._mom_force_close
            def confirmed_force_close(price):
                before = deepcopy({k:v for k,v in self.runner.st.items() if k.startswith('mom_')})
                force_close(price)
                quantity = sum(q for group, legs in self.broker.book.legs.items()
                               if group.startswith('MOM ') for _, q in legs)
                if quantity:
                    self.runner.st.update(before)
                    self.runner.st['mom_leg_shares'] = quantity
                    self.events.append(dict(day=self.broker.current_day,event='RETAIN_UNFILLED_MOM',quantity=quantity))
            stack.enter_context(patch.object(self.runner, '_mom_force_close', confirmed_force_close))
            wait = self.runner._wait_for_fill
            def terminal_partial(*args, **kwargs):
                status, delta = wait(*args, **kwargs)
                if status == 'PARTIAL':
                    self.broker.cancel_order(self.broker.last_order_id)
                return status, delta
            stack.enter_context(patch.object(self.runner, '_wait_for_fill', terminal_partial))

    def initialize(self):
        if self.active:
            self.runner._refresh_position()
            return
        self.daily_init()

    def before_event(self, day):
        st = self.runner.st
        previous = st.get('trade_date')
        if previous and previous != day and self.has_legs():
            state_quantity = (sum(q for _, q in st.get('short_legs', [])) +
                              sum(q for _, q in st.get('long_legs', [])) + st.get('mom_leg_shares', 0))
            ledger_quantity = sum(q for legs in self.broker.book.legs.values() for _, q in legs)
            if state_quantity != ledger_quantity:
                raise RuntimeError('carry adapter: strategy/execution quantities disagree: state={} ledger={} main={} mom={}'.format(
                    state_quantity, ledger_quantity, st.get('fstate'), st.get('mom_state')))
            self.active = True
            self.events.append(dict(day=day, event='CARRY', quantity=ledger_quantity,
                                    legs=deepcopy(self.broker.book.legs)))
            st['trade_date'] = day
            for key in ('trade_count_short', 'trade_count_long', 'mom_trade_count'):
                st[key] = 0
            st['day_pnl'] = 0.
            for key in ('price_history', 'mom_price_history'):
                if key in st: st[key].clear()
            # Cancel unfilled ladder monitoring without losing its existing exit leg.
            if st.get('short_legs') and st.get('fstate') not in (self.module.STATE_SOLD, getattr(self.module,'STATE_DIPPING','DIPPING')):
                st['fstate'] = self.module.STATE_SOLD
            if st.get('long_legs') and st.get('fstate') not in (self.module.STATE_BT_BOUGHT, getattr(self.module,'STATE_BT_SPIKING','BT_SPIKING')):
                st['fstate'] = self.module.STATE_BT_BOUGHT
            self.runner._refresh_position()
        if self.active and not self.has_legs():
            self.active = False
            self.events.append(dict(day=day, event='FLAT_REINITIALIZE'))
            st['trade_date'] = ''  # force original daily initialization using known current-day data
            self.daily_init()
        if self.active and self.version == 'v39':
            # v39's outer gate otherwise suppresses exits when entry capacity is zero.
            st['do_short'] = bool(st.get('short_legs'))
            st['do_long'] = bool(st.get('long_legs'))
