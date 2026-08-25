"""MiniQMT runtime orchestration for the pure DayTradingEngine."""
import time
import traceback
from datetime import datetime

from .domain import ExecutionResult, OrderStatus, PortfolioSnapshot
from infra.connector import (
    MiniQMTConnector,
    MockContextInfo,
    get_trade_detail_data,
    order_shares,
    set_global_conn,
)
from infra.logger import _log
from .adapters import MiniQmtExecutionAdapter


class MiniQmtRuntime:
    """Owns connection, time, planning, execution, and persistence."""

    def __init__(self, engine, execution_factory, plan_builder, state_store,
                 account, stock_code, stock_qmt, dry_run=False,
                 connector=None, clock=None, sleeper=None):
        self.engine = engine
        self.plan_builder = plan_builder
        self.state_store = state_store
        self.account = account
        self.stock_code = stock_code
        self.stock_qmt = stock_qmt
        self.dry_run = bool(dry_run)
        self.connector = connector or MiniQMTConnector()
        set_global_conn(self.connector, self.dry_run)
        self.context = MockContextInfo(self.connector)
        adapter = MiniQmtExecutionAdapter(
            connector=self.connector,
            context=self.context,
            account=account,
            stock_code=stock_qmt,
            portfolio_provider=self.portfolio_snapshot_dict,
            order_function=order_shares,
        )
        self.execution = execution_factory(adapter)
        self.clock = clock or time.time
        self.sleeper = sleeper or time.sleep
        self.running = True
        self.last_init_retry = 0.0
        self.last_heartbeat = 0.0
        self.opening_refresh_date = ''
        self.last_state_fingerprint = None

    def run(self):
        if not self.connector.connect_data():
            _log('[ERROR] market data connection failed')
            return
        if not self.dry_run and not self.connector.connect_trade(self.account):
            _log('[ERROR] trade connection failed')
            self.connector.disconnect()
            return
        _log('[START] standalone v32 runtime {} {}'.format(
            'SIGNAL' if self.dry_run else 'LIVE', self.stock_qmt))
        try:
            self.initialize_session()
            while self.running:
                self.run_once()
        except KeyboardInterrupt:
            _log('[STOP] interrupted by user')
        except Exception as exc:
            _log('[ERROR] {}'.format(exc))
            traceback.print_exc()
        finally:
            self.persist(force=True)
            self.connector.disconnect()
            if self.engine.has_open_legs:
                _log('[WARN] open intraday legs remain; reconcile the account manually')

    def run_once(self, now=None):
        timestamp = float(self.clock() if now is None else now)
        moment = datetime.fromtimestamp(timestamp)
        now_hms = moment.strftime('%H:%M:%S')
        today = moment.strftime('%Y%m%d')
        if not self._is_market_open(now_hms):
            if self.engine.session.trade_date and self.engine.session.trade_date != today:
                self.initialize_session(force=True, moment=moment)
            self.persist()
            self.sleeper(10)
            return
        if not self.engine.session.initialized:
            if timestamp - self.last_init_retry >= 10:
                self.last_init_retry = timestamp
                self.initialize_session(moment=moment)
            self.sleeper(1)
            return
        if self.opening_refresh_date != today:
            self.initialize_session(force=True, moment=moment)
            self.opening_refresh_date = today

        tick = self.current_tick()
        price = float(tick.get('lastPrice', 0) or 0)
        if price <= 0:
            self.sleeper(1)
            return
        actions = self.engine.on_tick(price, timestamp, now_hms)
        for intent in actions:
            if intent is not None:
                self.execute(intent)
        if (timestamp - self.last_heartbeat >=
                self.engine.settings.market_status_interval_sec):
            self.last_heartbeat = timestamp
            self._heartbeat(price, now_hms)
        self.persist()
        self.sleeper(0.5)

    def initialize_session(self, force=False, moment=None):
        moment = moment or datetime.now()
        trade_date = moment.strftime('%Y%m%d')
        restored = False
        if not force and not self.engine.session.initialized:
            saved = self.state_store.load_for_date(trade_date)
            if saved is None:
                latest = self.state_store.load_latest()
                if latest and (latest.get('short_legs') or
                               latest.get('long_legs') or
                               latest.get('mom_leg_shares')):
                    saved = latest
            if saved:
                self.engine.restore(saved)
                restored = True
        self.connector.refresh_daily_cache()
        bars = self.connector.get_daily_bars(self.plan_builder.history_length)
        tick = self.current_tick()
        portfolio = self.portfolio_snapshot()
        if not portfolio.valid:
            self.engine.session.initialized = False
            self.engine.session.reconcile_required = True
            self.engine.session.reconcile_reason = 'portfolio query failed during initialization'
            return False
        try:
            plan = self.plan_builder.build(
                bars, tick, portfolio, trade_date=moment.date())
        except Exception as exc:
            self.engine.session.initialized = False
            self.engine.session.reconcile_required = True
            self.engine.session.reconcile_reason = 'daily plan failed: {}'.format(exc)
            _log('[INIT] {}'.format(self.engine.session.reconcile_reason))
            return False
        carried_open_legs = bool(
            self.engine.has_open_legs and self.engine.session.trade_date and
            self.engine.session.trade_date != trade_date)
        if restored or self.engine.session.trade_date == trade_date or carried_open_legs:
            self.engine.update_plan(plan.signal, portfolio, plan.completed_closes)
            if carried_open_legs:
                self.engine.session.trade_date = trade_date
                self.engine.session.reconcile_required = True
                self.engine.session.reconcile_reason = (
                    'open intraday leg carried across trading date')
        else:
            self.engine.begin_session(
                trade_date, plan.signal, portfolio, plan.completed_closes)
        if plan.reconciliation_reason:
            self.engine.session.reconcile_required = True
            self.engine.session.reconcile_reason = plan.reconciliation_reason
        self.persist(force=True)
        _log('[PLAN] REV-T={} FWD-T={} sell={} buy={}'.format(
            self.engine.session.do_short,
            self.engine.session.do_long,
            plan.signal.get('sell_trigger'),
            plan.signal.get('buy_trigger')))
        return True

    def execute(self, intent):
        st = self.engine.session
        if st.reconcile_required:
            unknown_order = ('order' in st.reconcile_reason.lower() or
                             'cancel' in st.reconcile_reason.lower())
            if intent.effect.is_opening or unknown_order:
                result = ExecutionResult(OrderStatus.SKIPPED, 0)
                self.engine.apply_execution(intent, result)
                _log('[ORDER BLOCKED] {}: {}'.format(
                    intent.label, st.reconcile_reason))
                return result
        result = self.execution.execute(
            intent,
            reserved_sellable=self.engine.reserved_sellable_shares,
            dry_run=self.dry_run,
        )
        self.engine.apply_execution(intent, result)
        if not self.dry_run and result.signed_filled_shares:
            self.engine.update_portfolio(self.portfolio_snapshot())
        self.persist(force=True)
        _log('[ORDER {}] {} {} shares order_id={}'.format(
            result.status.value, intent.label,
            result.signed_filled_shares, result.order_id))
        return result

    def portfolio_snapshot_dict(self):
        snapshot = self.portfolio_snapshot()
        return {
            'shares': snapshot.shares,
            'can_use': snapshot.sellable,
            'cash': snapshot.cash,
            'cost': snapshot.cost,
            'price': snapshot.last_price,
            'valid': snapshot.valid,
        }

    def portfolio_snapshot(self):
        positions = get_trade_detail_data(self.account, 'STOCK', 'POSITION')
        position_ok = getattr(
            self.connector, 'last_position_query_ok', self.dry_run)
        shares = sellable = 0
        cost = 0.0
        for position in positions:
            if position.m_strInstrumentID == self.stock_code:
                shares = int(position.m_nVolume)
                sellable = int(getattr(
                    position, 'm_nCanUseVolume', position.m_nVolume))
                cost = float(position.m_dOpenPrice)
                break
        accounts = get_trade_detail_data(self.account, 'STOCK', 'ACCOUNT')
        account_ok = getattr(
            self.connector, 'last_account_query_ok', self.dry_run)
        cash = float(accounts[0].m_dAvailable) if accounts else 0.0
        tick = self.current_tick()
        price = float(tick.get('lastPrice', 0) or 0)
        return PortfolioSnapshot(
            shares, sellable, cash, cost, price,
            bool(self.dry_run or (position_ok and account_ok)))

    def current_tick(self):
        return self.context.get_full_tick([self.stock_qmt]).get(
            self.stock_qmt, {})

    def persist(self, force=False):
        value = self.engine.snapshot()
        fingerprint = repr(value)
        if not force and fingerprint == self.last_state_fingerprint:
            return
        self.state_store.save(value)
        self.last_state_fingerprint = fingerprint

    def _heartbeat(self, price, now_hms):
        st = self.engine.session
        details = ''
        closes = st.ma_completed_closes
        if len(closes) >= 19:
            ma5 = sum(closes[-4:] + [price]) / 5.0
            ma20 = sum(closes[-19:] + [price]) / 20.0
            details = ' ma5={:.2f} ma20={:.2f}'.format(ma5, ma20)
            if price < ma20 * self.engine.settings.ma20_risk_ratio:
                details += ' RISK_BELOW_MA20'
        _log('[HB {}] price={:.2f} state={} short={} long={} pnl={:,.0f}{}'.format(
            now_hms, price, st.fstate,
            sum(shares for _, shares in st.short_legs),
            sum(shares for _, shares in st.long_legs),
            st.total_pnl, details))

    @staticmethod
    def _is_market_open(now_hms):
        return ('09:30:00' <= now_hms <= '11:30:00' or
                '13:00:00' <= now_hms <= '15:00:00')
