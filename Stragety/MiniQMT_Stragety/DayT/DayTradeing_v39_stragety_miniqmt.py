# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT �� QMT day trading v39 + MOM��ȴʱ����εݼ�
================================================================================

 [v39 �Ķ�] (vs v38)
   - ���ֲ���ִ�У���������MOM���Ⱦ�Ҫ������1�ֿɽ�������
   - MOM��TҪ���ֽ�������ɲ�ͬʱ���㣬�����������T+1�޷�����
   - �������ӽ���ͣʱ�����������ȣ����岢�ȶ����ٽ��
   - ����ENABLED 0 lots��T+0�͸���need��������־

 [v38 �Ķ�] (vs v37)
   - ����ͳһ�� MiniQMT ��ǰ�����Ŀ¼��ȡ������ָ���ɰ� C:/QMT/datadir
   - ǰ��Ȩ��������ָ�꣬δ��Ȩ�������� tick lastClose ����У��
   - �޳�����δ������߲�У����һ�����գ�ʧ��ʱ����ȫ������
   - ���� tick open �������봥���ۼ��㣬���ٸ�����ʷ open ����

 [v37 �Ķ�] (vs v36)
   - MOM�״�ƽ����ȴ12�룬����ÿ�������ݼ�2��
   - ��ȴ����Ϊ12/10/8/6/4/2�룬�ﵽ2��󱣳����2��

 [v36 �Ķ�] (vs v35)
   - MOM��߻���׼������ʱ����ȴ12�룻�۸��Ը������ش�����������
   - ��ȴ����ڡ��������ش����ۡ������������0.5%����������
   - MOM̽�׻���׼�����ʱ���öԳ���ȴ����������Ч�۲�����������

 [v35 �Ķ�] (vs v34)
   - ��T�����󣬼۸��״���̽��������99%ʱ��������1
   - ��δ����ԭ��ش����۶������ٴδﵽ������99%����������һ��FIX���

 [v34 �Ķ�] (vs v33)
   - MOMֻ��REV-T���ȴ����ҵ���������ֵʱ��Ȩ
   - ��ǰ�۴ﵽ�򳬹�REV-T������ֵ�󣬲�����Ȩ��������Ȩ״̬�����ͷ�

 [v33 �Ķ�] (vs v32)
   - MOM�ӽ�����REV-T������ʱ��Ȩ������ͬһ�������ظ�����
   - MOM��߻���ʹ�ö�������������Ӧ�Ļس���ֵ������ǿ�ƹ���������0.1%
   - �����Ż��ֱ��ɶ����������ؿ��ƣ��رպ�ָ�v32��Ϊ

 [v32 �Ķ�] (vs v31)
   - MOM_ATR_MULT ��3.5��ߵ�4.0�����Ͷ����ڲ����µ��󴥷�Ƶ��

 [v31 �Ķ�] (vs v30)
   - MOM/REV-T ��ظ�����һ��FIX�޼ۣ�������ּ۰���ͣ�۶����ʽ�
   - askPrice�̿�ȱʧʱ���˵������ۣ�����FIX���ۼ�¼�������

 [v29 �Ķ�] (vs v28)
   �� MOM���߻������� �� ���� MOM_ENABLED=False ����, ��ȫ���ζ��߶�����ת����:
     ���ɼ�tick�۸���ʷ�����µ�������ӡ����, ��������״̬��(��T/��T)

 [v28 �Ķ�] (vs v27)
   �� sell_trigger �����ĸ��� �� ��T������ɺ�(��ء�IDLE), �� sell_trigger ������
     ʵ��������(sell_fill_price), ��������������ʾ�ѳɽ����ľ���ֵ

 [v27 �Ķ�] (vs v26)
   �� ������ؿ��� �� ���߶�����T�Ľ�����ػ���(����3%ֹ�����)���ӿ���
     MOM_EMERGENCY_BUYBACK_ENABLED, Ĭ�� False (�ر�)

 [v26 �Ķ�] (vs v25)
   �� ������������Ӧ �� 2�����ǵ���ֵ�ɹ̶�2%��Ϊ 2�����10����ATR,
     �沨�����Զ����� (�Ͳ�������ֵ��խ���߲�������ֵ�ſ�), ����������
     MOM_TRIGGER_MIN_PCT=1% / MOM_TRIGGER_MAX_PCT=6% ������

 [v25 �Ķ�] (vs v24)
   �� ���߶�����ת���� �� ���������������źŵ��¼���������:
     �� 2�����ǡ���ֵ �� ��߻�������(�������1��) �� ���۵�1.2% �� ̽�׻�������(���������)
     �� 2���ӵ�����ֵ �� ̽�׻�������(��������1��) �� �����1.5% �� ��߻�������(���������)
     �� �������� MOM_LOT_SIZE=1��(200��), �����ս������� MOM_MAX_DAILY_TRADES
     �� �������Ʋ��ж�������, �µ�ǰͬ������λ/�ֽ�ǯ��, β��ǿ��ƽ��������

 [v24 �Ķ�] (vs v23)
   �� ������־�Ż� �� [HB] IDLE �е��۸���Խ����ش�����ʱ, �� need ���ӡ
     ! ��ʾ�Ѵ��� (REV-T �ǳ����� / FWD-T �������), ������ need ֵ��������ʾ

 [v23 �Ķ�] (vs v22)
   �� �ϸ�ɽ��ж� �� _wait_for_fill ֻ��"����ɽ�"ʱ�гɹ�, ����
     FILLED / PARTIAL / TIMEOUT ��̬; ���ֳɽ�/δ�ɽ����ٱ�����Ϊ�ɹ�
   �� �µ�ǰ��λ��� �� ������"��������"�����밴"�ֽ��������"ǯ���µ���,
     ��λС�ڼƻ���ʱ��ʵ�ʲ�λ�µ� (����ֻ����������)
   �� �Ȱ�"ʵ�ʳɽ�����"���� �� short_legs / long_legs �� [��] ��Ϊ [(��,����)],
     ���/������ʵ�ʹ���ƽ��, ë���� ��(�۲������) ����
   �� δ�ɽ�/���ֳɽ��ָ� �� ����δ�ɽ��� IDLE, ���δ�ɽ����� SOLD �������;
     ��ʱ�Զ�����, ����ҵ�������������ɽ�

 [v22 �Ķ�] (vs v21)
   �� ��T���ݼ��� �� �����ɹ���, ���۸��������"���ۡ�(1+���ݷ���)", �����
     ��߻�����, ׷��������һ��(���Ϸ�������)
   �� ��T���ݼ��� �� ����ɹ���, ���۸��������"��ۡ�(1-���ݷ���)", �����
     ̽�׻������, ׷��������һ��(���·�������)
   �� ���Ȳ�λ���� �� ׷�ӵ�ÿ�ּ���δƽ����, ���/����ʱһ����ƽ��ȫ����

 [run mode]
 python "Stragety/MiniQMT_Stragety/DayTradeing_v39_stragety_miniqmt.py" --mode signal
 python "Stragety/MiniQMT_Stragety/DayTradeing_v39_stragety_miniqmt.py" --mode live
 python "Stragety/MiniQMT_Stragety/DayTradeing_v39_stragety_miniqmt.py" --mode backtest

================================================================================
"""
import os, sys, time as _time, argparse, traceback as _traceback
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
import numpy as np, pandas as pd

from core import config as cfg
from core.signals import compute_signal
from Stragety.MiniQMT_Stragety.DayT.infra.logger import FileLogger, set_logger, get_logger, _log
from Stragety.MiniQMT_Stragety.DayT.infra.connector import (
    MiniQMTConnector, MockContextInfo,
    get_trade_detail_data, order_shares, set_global_conn,
)

ACCOUNT = cfg.ACCOUNT; STOCK_CODE = cfg.STOCK_CODE; STOCK_NAME = cfg.STOCK_NAME
STOCK_QMT = cfg.STOCK_QMT; TRADE_LOT_SIZE = cfg.TRADE_LOT_SIZE
STATE_IDLE = cfg.STATE_IDLE; STATE_SPIKING = cfg.STATE_SPIKING
STATE_SOLD = cfg.STATE_SOLD; STATE_DIPPING = cfg.STATE_DIPPING
STATE_DONE = cfg.STATE_DONE; STATE_FORCED = cfg.STATE_FORCED
STATE_BT_DIPPING = cfg.STATE_BT_DIPPING; STATE_BT_BOUGHT = cfg.STATE_BT_BOUGHT
STATE_BT_SPIKING = cfg.STATE_BT_SPIKING
MOM_STATE_REV_YIELD = 'MOM_REV_YIELD'
MOM_STATE_BUYBACK_COOLING = 'MOM_BUYBACK_COOLING'
MOM_STATE_SELLBACK_COOLING = 'MOM_SELLBACK_COOLING'

# �� v35: ��T�������ȵ�������99%��δ����ԭ��ؼۼ�������99%ʱ���
REV_REBOUND_BUYBACK_RATIO = 0.99

# �� v22: ���ݼӲ�/���ֲ��� (�ɹ�����/�����, �ڳɽ��ۻ����ϼӼ��ۼ��׷��)
LADDER_UP_STEP_PCT   = 0.015   # ��T: ������۸����� +1.5% �� ׷�ӳ�߻�������
LADDER_DOWN_STEP_PCT = 0.015   # ��T: �����۸��ٵ� -1.5% �� ׷��̽�׻�������

# �� v23: �ɽ��ж�
FILL_TIMEOUT_SEC = 8.0   # �ȴ�����ɽ��ĳ�ʱ����

# �� v25/v26: ���߶�����ת���� (2�����¼�����, �����������ź�)
# v31: MOM/REV-T buybacks use an explicit ask1 limit to avoid peer-price
# orders reserving cash at the daily upper-limit price.

MOM_ENABLED           = True
MOM_WINDOW_SEC        = 120           # ��ⴰ��: 2�����ڼ۸�仯
MOM_ATR_WINDOW_SEC    = 600           # ATR���㴰��: ���10����
MOM_ATR_MULT          = 3.6           # �������� = 2 �� ���10����ATR (����Ӧ)
MOM_TRIGGER_MIN_PCT   = 0.01          # ������������ 1% (�����Ͳ����չ��ȴ���)
MOM_TRIGGER_MAX_PCT   = 0.06          # ������������ 6% (�����߲������޷�����)
MOM_SHORT_BUYBACK_PCT = 0.015         # ��T: ������۸��1.2% �� ����̽�׻�������
MOM_LONG_SELLBACK_PCT = 0.018         # ��T: �����۸���1.5% �� ������߻�������
MOM_LOT_SIZE          = cfg.TRADE_LOT_SIZE_MOM  # ���߻��Ƶ������� = 1�� (100��)
MOM_MAX_DAILY_TRADES  = 3             # ���߻��Ƶ�������ȴ���(�����Ƚ���)
# �� v27: ������ؿ��� �� ��T����۸��ǳ�����3%ʱǿ�����ֹ��
MOM_EMERGENCY_BUYBACK_ENABLED = False # Ĭ�Ϲر�; True=�����������ֹ��
# �� v29: MOM�����ܿ��� �� False=��ȫ���ζ��߶�����ת, ���ɼ�tick���µ�

# �� v33-1: REV-T�ٽ�������ʱMOM��Ȩ���������أ�
MOM_REV_PRIORITY_ENABLED  = True       # True=MOM�ӽ�REV-T������ʱֹͣ��������Ȩ
MOM_REV_PRIORITY_BAND_PCT = 0.012      # REV-T�������·�1.2%�������Ȩ��

# �� v33-2: MOM��������������Ӧ�س�ȷ�ϣ��������أ�
MOM_ADAPTIVE_PULLBACK_ENABLED = True   # False=�ָ�ʹ��cfg.PULLBACK_PCT
MOM_PULLBACK_ATR_MULT         = 0.50   # �س���ֵȡ������Ӳ���ATR��50%
MOM_PULLBACK_MIN_PCT          = 0.0035 # ����0.35%�����˸߼۹��̿�΢�س�
MOM_PULLBACK_MAX_PCT          = 0.0060 # ����0.60%�����Ʒ�ֵ����

# �� v39: MOMƽ��ȷ����ȴ���״�12�룬����ÿ�ֵݼ�2�룬���2�룩
MOM_CLOSE_COOLDOWN_START_SEC = 12.0
MOM_CLOSE_COOLDOWN_STEP_SEC = 2.0
MOM_CLOSE_COOLDOWN_MIN_SEC = 2.0
MOM_CLOSE_MIN_PROFIT_PCT = 0.005

# v39: 601869 is a normal A-share.  Freeze new legs near the normal 10% upper
# limit, then require a meaningful opening-board retreat to persist for 120s.
LIMIT_UP_GUARD_PCT = 0.095
LIMIT_UP_RELEASE_PCT = 0.085
LIMIT_UP_RELEASE_HOLD_SEC = 120.0


def calculate_execution_capacity(base_can_use, available_cash, price,
                                 max_daily_trades):
    """Calculate executable REV/FWD lots; zero lots are never enabled."""
    sellable_shares = max(0, int(base_can_use or 0))
    sellable_lots = sellable_shares // TRADE_LOT_SIZE
    cash_lots = 0
    if price and price > 0:
        cash_lots = int(float(available_cash or 0) /
                        (float(price) * TRADE_LOT_SIZE * 1.01))
    short_lots = min(sellable_lots, max_daily_trades)
    long_lots = min(cash_lots, sellable_lots, max_daily_trades)
    can_short = short_lots >= 1
    can_long = long_lots >= 1

    short_reason = '' if can_short else 'sellable {} sh < {} sh'.format(
        sellable_shares, TRADE_LOT_SIZE)
    long_reasons = []
    if cash_lots < 1:
        long_reasons.append('insufficient cash for 1 lot')
    if sellable_lots < 1:
        long_reasons.append(
            'T+1: sellable base shares {} sh < {} sh'.format(
                sellable_shares, TRADE_LOT_SIZE))
    return {
        'short_lots': short_lots,
        'long_lots': long_lots,
        'cash_lots': cash_lots,
        'sellable_lots': sellable_lots,
        'can_short': can_short,
        'can_long': can_long,
        'short_reason': short_reason,
        'long_reason': '; '.join(long_reasons),
    }


def limit_up_guard_transition(active, release_since, price, last_close,
                              now_ts):
    """Return ``(active, release_since, event)`` for the new-leg guard."""
    if price <= 0 or last_close <= 0:
        return bool(active), float(release_since or 0.0), ''
    rise = price / last_close - 1.0
    if rise >= LIMIT_UP_GUARD_PCT:
        return True, 0.0, 'LOCK' if not active else ''
    if not active:
        return False, 0.0, ''
    if rise <= LIMIT_UP_RELEASE_PCT:
        if not release_since:
            return True, float(now_ts), 'RELEASE_PENDING'
        if now_ts - release_since >= LIMIT_UP_RELEASE_HOLD_SEC:
            return False, 0.0, 'UNLOCK'
        return True, float(release_since), ''
    return True, 0.0, ''



class StrategyRunner:
    """MiniQMT v39 �� MOM��ȴʱ����εݼ� + v36ȫ�����ơ�"""

    def __init__(self, dry_run=False):
        logger = get_logger()
        if logger is None:
            logger = FileLogger(STOCK_CODE, version='v39')
            set_logger(logger)
        self.conn = MiniQMTConnector()
        set_global_conn(self.conn, dry_run)
        self.ctx = MockContextInfo(self.conn)
        self.st = self.ctx.st
        self.dry_run = dry_run
        self._last_heartbeat = 0.0
        self._running = True
        self.total_t_days = 0
        self.total_pnl = 0.0

    def _init_state(self):
        self.st.update({
            'daily_signal': None, 'base_shares': 0, 'base_can_use': 0, 'base_cost': 0.0,
            'entry_price': 0.0, 'fstate': STATE_IDLE,
            'peak_price': 0.0, 'dip_price': 0.0,
            'sell_fill_price': 0.0, 'buyback_target': 0.0, 'buyback_target_pct': 0.0,
            'rebound_99_armed': False,
            'day_pnl': 0.0, 'stop_loss_hit': False,
            'total_t_days': self.total_t_days, 'total_pnl': self.total_pnl,
            'trade_date': '', '_guard_date': '',
            'initialized': False, 'init_attempts': 0, 'last_init_time': 0.0,
            'state_enter_time': '', 'sell_elapsed_bars': 0,
            'locked': False, 'lock_reason': '', 'lock_since': '',
            'lock_cooldown_until': 0.0, 'price_history': deque(),
            'limit_up_guard': False, 'limit_up_release_since': 0.0,
            '_pre_market_done': '', '_market_open_logged': False,
            # �� v22/v23: ���ݼӲ�/����״̬ �� �ȼ�¼Ϊ (�ɽ���, �ɽ�����)
            'ladder_sell_target': 0.0, 'ladder_buy_target': 0.0,
            'ladder_sold_count': 0, 'ladder_bought_count': 0,
            'short_legs': [], 'long_legs': [],
            # �� v25: ���߶�����ת����״̬ (��������״̬��)
            'mom_state': 'MOM_IDLE', 'mom_peak': 0.0, 'mom_dip': 0.0,
            'mom_sell_price': 0.0, 'mom_buy_price': 0.0,
            'mom_leg_shares': 0, 'mom_trade_count': 0,
            'mom_price_history': deque(), 'mom_last_hb': 0.0,
            # �� v26: ����ӦATR �� 10���Ӽ۸񴰿� + ��ǰ��������
            'mom_atr_history': deque(), 'mom_trigger_pct': 0.0,
            # �� v33: MOM��Ȩ״̬ + ����ʱ����Ķ����س�ȷ����ֵ
            'mom_rev_yield_trigger': 0.0, 'mom_pullback_pct': 0.0,
            # �� v36: MOMƽ����ȴ״̬
            'mom_cooldown_until': 0.0, 'mom_cooldown_trigger': 0.0,
            'mom_cooldown_cycles': 0, 'mom_cooldown_duration': 0.0,
            'mom_last_block_reason': '',
        })

    def _reset_daily(self):
        guard = self.st.get('_guard_date', '')
        bs, bu, bc = self.st.get('base_shares', 0), self.st.get('base_can_use', 0), self.st.get('base_cost', 0.0)
        ep = self.st.get('entry_price', 0.0)
        self._init_state()
        self.st['base_shares'] = bs; self.st['base_can_use'] = bu; self.st['base_cost'] = bc
        self.st['entry_price'] = ep; self.st['_guard_date'] = guard
        self.st['total_t_days'] = self.total_t_days; self.st['total_pnl'] = self.total_pnl
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False; self.st['lock_reason'] = ''; self.st['lock_since'] = ''
        self.st['_pre_market_done'] = ''; self.st['_market_open_logged'] = False

    def _lock_all_trading(self, reason):
        """Fail closed when the daily-data initialization path is unhealthy."""
        self.st['daily_signal'] = None
        self.st['do_short'] = False
        self.st['do_long'] = False
        self.st['initialized'] = False
        self.st['locked'] = True
        self.st['lock_reason'] = reason
        self.st['lock_since'] = cfg.now_hms()
        _log('[TRADE-LOCK] {}; all trading disabled'.format(reason))

    def _update_limit_up_guard(self, price, tick_data, now_ts):
        last_close = float(tick_data.get('lastClose', 0) or 0)
        active, release_since, event = limit_up_guard_transition(
            self.st.get('limit_up_guard', False),
            self.st.get('limit_up_release_since', 0.0),
            float(price or 0), last_close, now_ts)
        self.st['limit_up_guard'] = active
        self.st['limit_up_release_since'] = release_since
        rise_pct = (price / last_close - 1.0) * 100 if last_close > 0 else 0.0
        if event == 'LOCK':
            _log('[LIMIT-UP GUARD] Y{:.2f} / lastClose Y{:.2f} ({:+.2f}%) '
                 '-> freeze all new legs'.format(price, last_close, rise_pct))
        elif event == 'RELEASE_PENDING':
            _log('[LIMIT-UP GUARD] opening-board retreat {:+.2f}%; hold {:.0f}s '
                 'before release'.format(rise_pct, LIMIT_UP_RELEASE_HOLD_SEC))
        elif event == 'UNLOCK':
            _log('[LIMIT-UP GUARD RELEASE] retreat stable for {:.0f}s; '
                 'new legs enabled by capacity'.format(
                     LIMIT_UP_RELEASE_HOLD_SEC))
        return active

    def _new_leg_block_reason(self):
        if self.st.get('locked', False):
            return self.st.get('lock_reason', 'strategy locked')
        if self.st.get('limit_up_guard', False):
            return 'near upper limit guard active'
        return ''

    def _mom_log_block(self, reason):
        if self.st.get('mom_last_block_reason', '') != reason:
            self.st['mom_last_block_reason'] = reason
            _log('[MOM BLOCKED] {}'.format(reason))

    def _daily_init(self):
        today = datetime.now().strftime('%Y%m%d')
        if self.st.get('trade_date', '') == today and self.st.get('initialized', False):
            self._refresh_position(); return
        is_new_day = self.st.get('trade_date', '') and self.st['trade_date'] != today
        saved_trail = self.st.get('bt_max_trail', 0) if not is_new_day else 0
        saved_history = self.st.get('price_history', []) if not is_new_day else []
        self._reset_daily()
        self.st['trade_date'] = today; self.st['_guard_date'] = today
        if not is_new_day and saved_trail > 0:
            self.st['bt_max_trail'] = saved_trail; self.st['price_history'] = saved_history

        # �� ����ˢ�����߻��棬ȷ��ָ�������������
        # ��ǰʱ��۸���������ʷ���߷ֿ���������ֹ�� tick ������ʷK�ߡ�
        tick_data = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {})
        today_open = float(tick_data.get('open', 0) or 0)
        curr_price_now = float(tick_data.get('lastPrice', 0) or 0)
        last_close = float(tick_data.get('lastClose', 0) or 0)
        self._update_limit_up_guard(
            curr_price_now, tick_data, _time.time())

        self.conn.refresh_daily_cache()
        snapshot = self.conn.load_daily_snapshot(
            cfg.HIST_DATA_LEN, today=today, tick_last_close=last_close,
            retries=3, retry_delay=1.0)
        if snapshot is None:
            self._refresh_position()
            self._lock_all_trading('daily data unavailable or stale')
            return

        hist = snapshot['adjusted']
        if len(hist) < 60:
            self._lock_all_trading(
                'complete daily bars {} < 60'.format(len(hist)))
            return
        self._refresh_position()
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        if self.st.get('entry_price', 0) == 0.0:
            self.st['entry_price'] = self.st.get('base_cost', 0.0)

        opens_list = hist['open'].astype(float).tolist()
        highs_list = hist['high'].astype(float).tolist()
        lows_list = hist['low'].astype(float).tolist()
        closes_list = hist['close'].astype(float).tolist()
        volume_list = hist['volume'].astype(float).tolist()
        signal_open = today_open if today_open > 0 else last_close
        _log('[DATA-DBG] last_complete={} hist_open[-3:]={} | tick_open={} | '
             'lastClose={} | signal_open={}'.format(
                 snapshot['last_complete_date'],
                 opens_list[-3:] if len(opens_list) >= 3 else opens_list,
                 today_open, last_close, signal_open))
        _log('[VOLUME-DATA] complete_count={} tail={}'.format(
            len(volume_list), volume_list[-3:] if len(volume_list) >= 3 else volume_list))

        signal = compute_signal(
            opens_list, highs_list, lows_list, closes_list, volume_list,
            yesterday_close=last_close, today_open=signal_open)
        if signal is None: return
        open_price = signal['open_price']
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        avail_cash = account[0].m_dAvailable if account else 0.0
        if curr_price_now <= 0: curr_price_now = open_price
        pos_value = base_shares * curr_price_now
        total_asset = pos_value + avail_cash
        pos_pct = pos_value / total_asset * 100 if total_asset > 0 else 0
        capacity = calculate_execution_capacity(
            base_can_use, avail_cash, curr_price_now, cfg.MAX_DAILY_TRADES)
        short_lots = capacity['short_lots']
        long_lots = capacity['long_lots']

        do_short = signal['do_short'] and capacity['can_short']
        short_reason = ''
        if not signal['do_short']:
            short_reason = signal.get('blocked_reason', 'signal blocked')
        elif not capacity['can_short']:
            short_reason = capacity['short_reason']
        do_long = capacity['can_long']
        long_reason = capacity['long_reason'] if not do_long else ''

        buy_trigger_floor = round(open_price * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
        buy_trigger_trail = round(curr_price_now * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
        buy_trigger = max(buy_trigger_floor, buy_trigger_trail)
        sellback_target_hint = round(buy_trigger * (1.0 + cfg.SELLBACK_RISE_PCT), 2)

        signal['do_short'] = do_short; signal['short_reason'] = short_reason
        signal['buy_trigger'] = buy_trigger; signal['buy_trigger_floor'] = buy_trigger_floor
        signal['buy_trigger_trail'] = buy_trigger_trail
        signal['sellback_target_hint'] = sellback_target_hint

        self.st['daily_signal'] = signal
        self.st['do_short'] = do_short; self.st['do_long'] = do_long
        self.st['long_reason'] = long_reason; self.st['short_lots'] = short_lots
        self.st['long_lots'] = long_lots; self.st['pos_value'] = pos_value
        self.st['pos_pct'] = pos_pct; self.st['avail_cash'] = avail_cash
        self.st['trade_count_short'] = 0; self.st['trade_count_long'] = 0

        for k in ('fstate', 'peak_price', 'dip_price', 'sell_fill_price', 'buyback_target', 'buyback_target_pct'):
            self.st[k] = STATE_IDLE if k == 'fstate' else 0.0
        self.st['day_pnl'] = 0.0; self.st['stop_loss_hit'] = False
        self.st['state_enter_time'] = cfg.now_hms(); self.st['sell_elapsed_bars'] = 0
        self.st['initialized'] = True
        for k in ('bt_dip_price', 'bt_buy_trigger', 'bt_buy_fill_price',
                   'bt_sellback_target', 'bt_max_trail', 'bt_sell_peak_price'):
            self.st[k] = 0.0
        self.st['locked'] = False; self.st['lock_reason'] = ''; self.st['lock_since'] = ''

    def _refresh_position(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        found = False
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                self.st['base_shares'] = pos.m_nVolume
                self.st['base_can_use'] = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                self.st['base_cost'] = pos.m_dOpenPrice
                found = True
                break
        if not found:
            self.st['base_shares'] = 0
            self.st['base_can_use'] = 0
            self.st['base_cost'] = 0.0

    # �T�T�T v23: �µ�ǰ��λ/�ֽ��� + �ϸ�ɽ��ж� �T�T�T

    def _cur_price(self):
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if price <= 0:
            price = self.st.get('daily_signal', {}).get('open_price', 0)
        return price

    def _available_cash(self):
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        return account[0].m_dAvailable if account else 0.0

    def _paired_long_capacity(self, price):
        """Capacity for a same-day buy leg backed by still-sellable old shares."""
        self._refresh_position()
        main_reserved = self._leg_shares(self.st.get('long_legs', []))
        mom_state = self.st.get('mom_state', 'MOM_IDLE')
        mom_reserved = self.st.get('mom_leg_shares', 0) if mom_state in (
            'MOM_BT_BOUGHT', 'MOM_BT_SPIKING', MOM_STATE_SELLBACK_COOLING) else 0
        reserved = main_reserved + mom_reserved
        sellable = max(0, int(self.st.get('base_can_use', 0) or 0))
        pairing_shares = max(0, sellable - reserved)
        capacity = calculate_execution_capacity(
            pairing_shares, self._available_cash(), price, 1)
        capacity['reserved_long_shares'] = reserved
        capacity['pairing_shares'] = pairing_shares
        if pairing_shares < TRADE_LOT_SIZE:
            capacity['long_reason'] = (
                'T+1 sellable base shares pairing capacity {} sh '
                '(sellable {} - reserved {}) < {} sh'
                .format(pairing_shares, sellable, reserved, TRADE_LOT_SIZE))
        return capacity

    def _clamp_sell_shares(self, planned):
        """����ǰ��������λ: ʵ�ʿ��� = min(�ƻ�, base_can_use)��"""
        # ÿ����������ȯ�����¿�����Ϊ׼������������ǰ tick ���������档
        self._refresh_position()
        can_use = self.st.get('base_can_use', 0)
        return int(min(planned, can_use))

    def _clamp_buy_shares(self, planned, price):
        """����ǰ����ֽ�: ʵ�ʿ��� = min(�ƻ�, �ֽ�������)��"""
        if price <= 0:
            price = self._cur_price()
        avail = self._available_cash()
        if price <= 0:
            return 0
        max_by_cash = int(avail / (price * 1.001))
        return int(min(planned, max_by_cash))

    def _leg_shares(self, legs):
        return sum(s for _, s in legs)

    def _leg_avg_price(self, legs):
        sh = self._leg_shares(legs)
        return sum(p * s for p, s in legs) / sh if sh > 0 else 0.0

    def _short_gross(self, legs, buyback_price):
        """��Të�� = ��(�������� - ��ؼ�) �� ���ȹ�����"""
        return sum((p - buyback_price) * s for p, s in legs)

    def _buyback_limit_price(self, fallback_price):
        """Use ask1 directly; fall back to the trigger price if ask1 is absent."""
        tick = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {})
        ask_prices = tick.get('askPrice', []) or []
        ask1 = float(ask_prices[0]) if len(ask_prices) > 0 and ask_prices[0] else 0.0
        base_price = ask1 if ask1 > 0 else float(fallback_price or 0.0)
        if base_price <= 0:
            return 0.0
        return round(base_price, 2)

    def _submit_buyback_order(self, shares, fallback_price, label):
        limit_price = self._buyback_limit_price(fallback_price)
        if limit_price <= 0:
            _log('[{} SKIP] FIX buyback price unavailable'.format(label))
            return 'SKIP', 0
        self._last_buyback_price = limit_price
        _log('[FIX-BUYBACK] trigger Y{:.2f} -> ask1/fallback limit Y{:.2f}'.format(
            fallback_price, limit_price))
        return self._submit_order(shares, limit_price, label, style='FIX')

    def _submit_order(self, shares, price, label, style='COMPETE'):
        """�µ� + �ȴ��ɽ���shares>0 ����, <0 ������

        �µ�ǰ�����ò�λ(����)/�ֽ�(����), ��ʵ�ʿ��µ������µ���
        ���� (status, actual_delta):
          status: 'FILLED' ���� | 'PARTIAL' ���� | 'TIMEOUT' δ�ɽ� | 'SKIP' �޿���
          actual_delta: ʵ�ʳɽ�����(������, ��������)
        """
        side = 'SELL' if shares < 0 else 'BUY'
        planned = abs(shares)
        if side == 'SELL':
            actual = self._clamp_sell_shares(planned)
        else:
            actual = self._clamp_buy_shares(planned, price)
        if actual < cfg.MIN_LOT:
            _log('[{} SKIP] {} ����: planned {} actual {}'.format(
                label, '����' if side == 'SELL' else '�ֽ�', planned, actual))
            return 'SKIP', 0
        signed = -actual if side == 'SELL' else actual
        snap = self._snapshot_account()
        price_str = 'MKT' if price <= 0 else 'Y{:.2f}'.format(price)
        _log('[ORDER-{}] {} �� {} sh'.format(label, price_str, actual))
        order_shares(STOCK_QMT, signed, style, price, self.ctx, ACCOUNT)
        return self._wait_for_fill(snap, signed, label, price, signed)

    # �T�T�T �ɽ�ȷ�� �T�T�T

    def _wait_for_fill(self, snap_before, expected_shares_delta,
                       label, trade_price, trade_shares, timeout_sec=FILL_TIMEOUT_SEC):
        """�ϸ�ȴ�����ɽ������� (status, actual_delta)��

        status: 'FILLED' ���� | 'PARTIAL' ���� | 'TIMEOUT' δ�ɽ���
        �κν���� base_shares/base_can_use/base_cost ������ͬ��������;
        TIMEOUT ʱ�Զ����������ҵ�, �����������ɽ���
        """
        if self.dry_run:
            self._verify_trade(snap_before, label, trade_price, trade_shares)
            return 'FILLED', expected_shares_delta
        waited = 0.0
        while waited < timeout_sec:
            _time.sleep(0.5); waited += 0.5
            curr_snap = self._snapshot_account()
            actual_delta = curr_snap['shares'] - snap_before['shares']
            if actual_delta == expected_shares_delta:
                self._verify_trade(snap_before, label, trade_price, trade_shares)
                return 'FILLED', actual_delta
        # ��ʱ: ��ͬ���ֲ�, �ж�����/δ�ɽ�
        self._verify_trade(snap_before, f'{label}(TIMEOUT)', trade_price, trade_shares)
        actual_delta = self.st['base_shares'] - snap_before['shares']
        if actual_delta != 0 and actual_delta * expected_shares_delta > 0:
            return 'PARTIAL', actual_delta
        self.conn.cancel_order(self.conn.last_order_id)
        return 'TIMEOUT', 0

    # �T�T�T ���� & У�� �T�T�T

    def _snapshot_account(self):
        positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
        shares = 0; can_use = 0; cost = 0.0
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                shares = pos.m_nVolume; can_use = getattr(pos, 'm_nCanUseVolume', pos.m_nVolume)
                cost = pos.m_dOpenPrice; break
        account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
        cash = account[0].m_dAvailable if account else 0.0
        tick = self.ctx.get_full_tick([STOCK_QMT])
        price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if price <= 0: price = self.st.get('daily_signal', {}).get('open_price', 0)
        return {'shares': shares, 'can_use': can_use, 'cash': cash,
                'cost': cost, 'total_asset': shares * price + cash, 'price': price}

    # �� v21: �ɽ���־�Ż� �� ������ӡ�۸������+�ֱֲ仯
    def _verify_trade(self, snap_before, label, trade_price, trade_shares):
        _time.sleep(0.3)
        snap_after = self._snapshot_account()
        d_shares = snap_after['shares'] - snap_before['shares']
        d_cash = snap_after['cash'] - snap_before['cash']
        # �� v22: ���ݶ��ֳɽ�ȷ�� (����ʱ��ԭ�߼�һ��)
        status = 'OK' if d_shares == trade_shares else ('PENDING' if d_shares == 0 else 'PARTIAL')

        # �� v21: [�ɽ�] �� �� �۸� �� ���� + �ֱֲ仯
        price_str = 'MKT' if trade_price <= 0 else 'Y{:.2f}'.format(trade_price)
        _log('[FILL-{}] {} �� {} sh | pos {}��{} | cash {:+,.0f}'.format(
            label, price_str, abs(trade_shares),
            snap_before['shares'], snap_after['shares'], d_cash))

        # �����쳣ʱ���У������
        if status != 'OK':
            _log('[VERIFY] {}: status {} | exp {:+d} act {:+d} | cash�� {:+,.0f}'.format(
                label, status, trade_shares, d_shares, d_cash))

        self.st['base_shares'] = snap_after['shares']
        self.st['base_can_use'] = snap_after['can_use']
        self.st['base_cost'] = snap_after['cost']

    # �T�T�T v21: �ź�+�ƻ��ϲ���� (�޷ָ���, �޿���) �T�T�T

    def _print_daily_brief(self, signal):
        trend = signal.get('trend', '?')
        trend_labels = {'strong_bull': 'STRONG-BULL', 'weak_bull': 'WEAK-BULL', 'sideways': 'SIDEWAYS', 'bear': 'BEAR'}
        open_p = signal.get('open_price', 0); close_y = signal.get('close_yday', 0)
        atr_pct = signal.get('atr_pct', 0) * 100; rsi_v = signal.get('rsi', 0)
        vol_r = signal.get('vol_ratio'); sell_mult = signal.get('sell_mult', 0)
        volume_valid = signal.get('volume_valid', False)
        vol_display = '{:.2f}'.format(vol_r) if volume_valid and vol_r is not None else 'N/A'
        sell_base = signal.get('sell_mult_base', 0); sell_trig = signal.get('sell_trigger', 0)
        range_capped = signal.get('range_capped', False)
        do_short = signal.get('do_short', False)
        base_shares = self.st.get('base_shares', 0); base_can_use = self.st.get('base_can_use', 0)
        pos_pct = self.st.get('pos_pct', 0); avail_cash = self.st.get('avail_cash', 0)
        do_long = self.st.get('do_long', False)
        short_lots = self.st.get('short_lots', 0); long_lots = self.st.get('long_lots', 0)

        tick = self.ctx.get_full_tick([STOCK_QMT])
        curr_price = tick.get(STOCK_QMT, {}).get('lastPrice', 0)
        if curr_price <= 0: curr_price = open_p
        pos_value = base_shares * curr_price
        trend_cn = trend_labels.get(trend, trend)

        # ��1: ����ָ��
        _log('[SIGNAL] {} | Open Y{:.2f} | ATR {:.1f}% | RSI {:.0f} | Vol_ratio {} | Mult {:.2f} | Trig Y{:.2f}{} {}'.format(
            trend_cn, open_p, atr_pct, rsi_v, vol_display, sell_mult, sell_trig,
            '(range-capped)' if range_capped else '',
            '[REV-T blocked:{}]'.format(
                signal.get('short_reason') or
                signal.get('blocked_reason', 'unknown')) if not do_short else ''))
        _log('[VOLUME] last {:.0f} / prev20_avg {:.0f} | n={} | {}'.format(
            signal.get('volume_current', 0), signal.get('volume_avg20', 0),
            signal.get('volume_baseline_count', 0), 'VALID' if volume_valid else 'INVALID-neutral'))

        # ����
        fd = signal.get('factor_details', {})
        if fd:
            _log('[FACTOR] {}'.format(' '.join('{} {:+.2f} | '.format(k, v) for k, v in fd.items())))

        # ��2: �ֲ� + ����
        bits = ['Position:{} sh Y{:,.0f}({:.0f}%)'.format(base_shares, pos_value, pos_pct),
                'Cash:Y{:,.0f}'.format(avail_cash),
                'Sellable:{} lots({} sh)'.format(
                    base_can_use // TRADE_LOT_SIZE, base_can_use)]
        _log('[ACCOUNT] {}'.format(' | '.join(bits)))

        # ��3-4: ��T / ��T
        guard_active = self.st.get('limit_up_guard', False)
        if guard_active:
            _log('[REV-T] FROZEN near upper limit; capacity {} lots'.format(
                short_lots))
        elif do_short:
            _log('[REV-T] ENABLED {} lots trig Y{:.2f} buyback=sell��(1-ATR%��{:.2f}) emerg +{:.0f}% / emergTrig{}'.format(
                short_lots, sell_trig, cfg.BUYBACK_TRIGGER_MULT, cfg.EMERGENCY_BUYBACK_PCT * 100,
                'True' if cfg.EMERGENCY_BUYBACK else 'False'))
        else:
            reason = signal.get('short_reason', signal.get('blocked_reason', 'unknown'))
            _log('[REV-T] BLOCKED {}'.format(reason))
        if guard_active:
            _log('[FWD-T] FROZEN near upper limit; capacity {} lots'.format(
                long_lots))
        elif do_long:
            buy_trig = signal.get('buy_trigger', 0)
            sell_hint = signal.get('sellback_target_hint', 0)
            _log('[FWD-T] ENABLED {} lots buy Y{:.2f} sell Y{:.2f}(+{:.1f}%) 1 lot��Y{:,.0f}'.format(
                long_lots, buy_trig, sell_hint, cfg.SELLBACK_RISE_PCT * 100, curr_price * TRADE_LOT_SIZE))
        else:
            _log('[FWD-T] BLOCKED {}  1 lot��Y{:,.0f}'.format(self.st.get('long_reason', 'unknown'), curr_price * TRADE_LOT_SIZE))

        if MOM_ENABLED:
            _log('[MOM] {}minutes��{:.1f}��ATR in 10 minutes({:.1f}%~{:.1f}%) ��T���-{:.1f}% ��T����+{:.1f}% MOM_LOT_SIZE({}sh) ����{}�� �������{}'.format(
                MOM_WINDOW_SEC // 60, MOM_ATR_MULT,
                MOM_TRIGGER_MIN_PCT * 100, MOM_TRIGGER_MAX_PCT * 100,
                MOM_SHORT_BUYBACK_PCT * 100, MOM_LONG_SELLBACK_PCT * 100,
                MOM_LOT_SIZE, MOM_MAX_DAILY_TRADES,
                '��' if MOM_EMERGENCY_BUYBACK_ENABLED else '��'))
            mom_capacity = calculate_execution_capacity(
                base_can_use, avail_cash, curr_price, 1)
            if guard_active:
                _log('[MOM-CAPACITY] FROZEN near upper limit')
            else:
                _log('[MOM-CAPACITY] short {} | long {}'.format(
                    'ON' if mom_capacity['can_short'] else
                    'OFF {}'.format(mom_capacity['short_reason']),
                    'ON' if mom_capacity['can_long'] else
                    'OFF {}'.format(mom_capacity['long_reason'])))
            _log('[MOM-v39] REV��Ȩ{} band {:.2f}% (������REV��ֵ) | ����Ӧ�س�{} {:.2f}~{:.2f}% ATR��{:.2f}'.format(
                '��' if MOM_REV_PRIORITY_ENABLED else '��', MOM_REV_PRIORITY_BAND_PCT * 100,
                '��' if MOM_ADAPTIVE_PULLBACK_ENABLED else '��',
                MOM_PULLBACK_MIN_PCT * 100, MOM_PULLBACK_MAX_PCT * 100,
                MOM_PULLBACK_ATR_MULT))
        else:
            _log('[MOM] ������ (MOM_ENABLED=False)')

        # �ۼ�
        if self.total_t_days > 0:
            _log('[CUM] {} trades gross~Y{:,.0f}'.format(self.total_t_days, self.total_pnl))

    # �T�T�T ״̬�� �T�T�T

    def _handle_idle(self, price):
        st = self.st; signal = st.get('daily_signal', {})
        if self._new_leg_block_reason():
            return
        if st.get('do_short', False):
            trigger = signal.get('sell_trigger', 999999)
            if price >= trigger:
                can_use = st.get('base_can_use', st['base_shares'])
                if can_use < cfg.MIN_LOT: return
                tc = st.get('trade_count_short', 0)
                if tc >= cfg.MAX_DAILY_TRADES or st.get('locked', False): return
                st['trade_count_short'] = tc + 1
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[REV-T spike #{}/{}] Y{:.2f} >= Y{:.2f}'.format(tc + 1, cfg.MAX_DAILY_TRADES, price, trigger))
                return
        if st.get('do_long', False):
            buy_trigger = signal.get('buy_trigger', 0)
            if price <= buy_trigger:
                tc = st.get('trade_count_long', 0)
                if tc >= cfg.MAX_DAILY_TRADES: return
                st['trade_count_long'] = tc + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['bt_buy_trigger'] = buy_trigger; st['state_enter_time'] = cfg.now_hms()
                _log('[FWD-T dip #{}/{}] Y{:.2f} <= Y{:.2f}(-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, buy_trigger, (buy_trigger - price) / buy_trigger * 100))

    def _handle_spiking(self, price):
        st = self.st
        if price > st['peak_price']: st['peak_price'] = price
        peak = st['peak_price']; pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            block_reason = self._new_leg_block_reason()
            if block_reason:
                _log('[REV-T ARM CANCELED] {}'.format(block_reason))
                st['trade_count_short'] = max(
                    0, st.get('trade_count_short', 0) - 1)
                st['fstate'] = STATE_IDLE
                st['peak_price'] = 0.0
                return
            _log('[REV-T sell trig] peak Y{:.2f} pullback {:.2f}% �� Y{:.2f}'.format(peak, pullback * 100, price))
            atr_pct = st['daily_signal']['atr_pct']; buyback_pct = atr_pct * cfg.BUYBACK_TRIGGER_MULT
            buyback_target = round(price * (1.0 - buyback_pct), 2)
            st['buyback_target'] = buyback_target
            st['buyback_target_pct'] = buyback_pct * 100
            st['sell_elapsed_bars'] = 0; st['state_enter_time'] = cfg.now_hms()
            # �� v23: �µ�ǰ��������λ, ��ʵ�ʿ��������µ�
            status, delta = self._submit_order(-TRADE_LOT_SIZE, price, 'REV-T sell')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[REV-T sell TIMEOUT] δ�ɽ�, �� IDLE')
                st['trade_count_short'] = max(0, st.get('trade_count_short', 0) - 1)
                st['fstate'] = STATE_IDLE
                return
            # FILLED / PARTIAL: ��ʵ�ʳɽ���������
            actual_sold = -delta
            st['sell_fill_price'] = price
            st['rebound_99_armed'] = False
            st['short_legs'].append((price, actual_sold))
            st['ladder_sell_target'] = round(price * (1.0 + LADDER_UP_STEP_PCT), 2) if status == 'FILLED' else 0.0
            if status == 'PARTIAL':
                _log('[REV-T sell PARTIAL] ʵ������ {} sh'.format(actual_sold))
            st['fstate'] = STATE_SOLD

    def _handle_sold(self, price):
        st = self.st; sp = st['sell_fill_price']; bt = st['buyback_target']
        # �� v22: ���ݼ��� �� �۸���������һ��(����+���ݷ���) �� ׷�ӳ�߻�������
        ladder = st.get('ladder_sell_target', 0.0)
        if ladder > 0 and price >= ladder:
            tc = st.get('trade_count_short', 0)
            can_use = st.get('base_can_use', st['base_shares'])
            if (can_use >= cfg.MIN_LOT and tc < cfg.MAX_DAILY_TRADES and
                    not self._new_leg_block_reason()):
                st['trade_count_short'] = tc + 1
                st['ladder_sold_count'] = st.get('ladder_sold_count', 0) + 1
                st['fstate'] = STATE_SPIKING; st['peak_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[REV-T ladder sell #{}/{}] Y{:.2f} >= Y{:.2f}(sell+{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_UP_STEP_PCT * 100))
                return
        if cfg.EMERGENCY_BUYBACK and price >= sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT):
            _log('[EMERG buyback trig] Y{:.2f}��Y{:.2f}(+{:.2f}%)'.format(sp, price, (price - sp) / sp * 100))
            self._do_buyback(price, 'EMERG'); return
        tightened_bt = bt
        if st['sell_elapsed_bars'] > 30 and price > sp * 0.995:
            tightened_bt = sp * (1.0 - st['daily_signal']['atr_pct'] *
                                 cfg.BUYBACK_TRIGGER_MULT * cfg.BUYBACK_TIGHTEN_MULT)
            tightened_bt = round(max(tightened_bt, bt), 2)
        # ԭ��ش��������ȣ�һ����������������v34��̽�׻���������̡�
        if price <= tightened_bt:
            st['fstate'] = STATE_DIPPING; st['dip_price'] = price
            st['state_enter_time'] = cfg.now_hms()
            _log('[Buyback trig {}] Y{:.2f}(-{:.2f}%)'.format(
                '(tightened)' if tightened_bt > bt else '', price, (sp - price) / sp * 100))
            return

        # �� v35: 99%���λ�����ء�
        # ����ԭ��ؼ۵���99%��ʱ�ſ��ܳ������״����µ���ֻ���棬
        # ���δ����ԭ��ؼ۱����ϻص����ߣ���ִ����һ��FIX��ء�
        rebound_price = round(sp * REV_REBOUND_BUYBACK_RATIO, 2)
        if not st.get('rebound_99_armed', False):
            if bt < rebound_price and price <= rebound_price:
                st['rebound_99_armed'] = True
                _log('[REV-T 99% armed] Y{:.2f} <= Y{:.2f}; buyback target Y{:.2f} not reached'.format(
                    price, rebound_price, bt))
            return
        if price >= rebound_price:
            _log('[REV-T 99% rebound] Y{:.2f} >= Y{:.2f} -> ask1 FIX buyback'.format(
                price, rebound_price))
            self._do_buyback(price, 'REBOUND99')

    def _handle_dipping(self, price):
        st = self.st
        if price < st['dip_price']: st['dip_price'] = price
        dip = st['dip_price'] or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            legs = st['short_legs'] or [(st['sell_fill_price'], TRADE_LOT_SIZE)]
            total_shares = self._leg_shares(legs)
            _log('[REV-T buyback trig] low Y{:.2f} bounce {:.2f}% �� Y{:.2f}'.format(
                dip, bounce * 100, price))
            bought = self._do_buyback(price, 'NORMAL')
            if bought >= total_shares and total_shares > 0:
                buyback_price = getattr(self, '_last_buyback_price', price)
                gross = self._short_gross(legs, buyback_price)
                self.total_t_days += 1
                self.total_pnl += gross
                _log('[REV-T done] buyback Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                    buyback_price, bought, gross))

    def _do_buyback(self, price, reason=''):
        st = self.st
        # �� v23: һ�������ȫ��δƽ�ַ�T�� (��ʵ�ʹ���)
        legs = st['short_legs'] or [(st.get('sell_fill_price', price), TRADE_LOT_SIZE)]
        shares = self._leg_shares(legs)
        if shares <= 0:
            return 0
        status, delta = self._submit_buyback_order(
            shares, price, 'REV-T buyback({})'.format(reason))
        bought = delta if delta > 0 else 0
        if bought <= 0:
            _log('[Buyback {}-FAIL] δ�ɽ�, ���� SOLD �������'.format(reason))
            st['fstate'] = STATE_SOLD
            return 0
        if bought >= shares:
            st['short_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_sold_count'] = 0
            st['fstate'] = STATE_DONE
            self._maybe_resume_trading()
            return bought
        # �������: ����δ��ز��ּ������
        remaining = shares - bought
        st['short_legs'] = [(st.get('sell_fill_price', price), remaining)]
        st['ladder_sell_target'] = 0.0
        st['fstate'] = STATE_SOLD
        _log('[Buyback PARTIAL] ����� {} sh, ʣ�� {} sh �������'.format(bought, remaining))
        return bought

    def _force_buyback(self):
        _log('[FORCE buyback trig]')
        st = self.st
        shares = self._leg_shares(st['short_legs']) or TRADE_LOT_SIZE
        price = self._cur_price()
        self._submit_buyback_order(shares, price, 'REV-T force buyback')
        st['short_legs'] = []; st['ladder_sell_target'] = 0.0; st['ladder_sold_count'] = 0
        st['fstate'] = STATE_FORCED

    def _handle_bt_dipping(self, price):
        st = self.st
        if price < st.get('bt_dip_price', price): st['bt_dip_price'] = price
        dip = st.get('bt_dip_price', price) or price; bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            block_reason = self._new_leg_block_reason()
            if block_reason:
                _log('[FWD-T ARM CANCELED] {}'.format(block_reason))
                st['trade_count_long'] = max(
                    0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                st['bt_dip_price'] = 0.0
                return
            capacity = self._paired_long_capacity(price)
            if not capacity['can_long']:
                _log('[FWD-T BLOCKED] {}'.format(capacity['long_reason']))
                st['trade_count_long'] = max(
                    0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                return
            _log('[FWD-T buy trig] low Y{:.2f} bounce {:.2f}% �� Y{:.2f}'.format(dip, bounce * 100, price))
            # �� v23: �µ�ǰ����ֽ�, ��ʵ�ʿ��������µ�
            status, delta = self._submit_order(TRADE_LOT_SIZE, price, 'FWD-T buy')
            if status in ('SKIP', 'TIMEOUT'):
                st['trade_count_long'] = max(0, st.get('trade_count_long', 0) - 1)
                st['fstate'] = STATE_BT_BOUGHT if st.get('long_legs') else STATE_IDLE
                return
            st['fstate'] = STATE_BT_BOUGHT
            st['bt_buy_fill_price'] = price
            st['long_legs'].append((price, delta))
            avg_bp = self._leg_avg_price(st['long_legs'])
            st['bt_sellback_target'] = round(avg_bp * (1.0 + cfg.SELLBACK_RISE_PCT), 2)
            st['ladder_buy_target'] = round(price * (1.0 - LADDER_DOWN_STEP_PCT), 2) if status == 'FILLED' else 0.0
            if status == 'PARTIAL':
                _log('[FWD-T buy PARTIAL] ʵ������ {} sh'.format(delta))

    def _handle_bt_bought(self, price):
        st = self.st; target = st.get('bt_sellback_target', 999999); bp = st.get('bt_buy_fill_price', 0)
        # �� v22: ���ݼ��� �� �۸��������һ��(���-���ݷ���) �� ׷��̽�׻�������
        ladder = st.get('ladder_buy_target', 0.0)
        if ladder > 0 and price <= ladder:
            tc = st.get('trade_count_long', 0)
            account = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')
            avail = account[0].m_dAvailable if account else 0.0
            if (avail >= price * TRADE_LOT_SIZE * 1.01 and
                    tc < cfg.MAX_DAILY_TRADES and
                    not self._new_leg_block_reason()):
                st['trade_count_long'] = tc + 1
                st['ladder_bought_count'] = st.get('ladder_bought_count', 0) + 1
                st['fstate'] = STATE_BT_DIPPING; st['bt_dip_price'] = price
                st['state_enter_time'] = cfg.now_hms()
                _log('[FWD-T ladder buy #{}/{}] Y{:.2f} <= Y{:.2f}(buy-{:.2f}%)'.format(
                    tc + 1, cfg.MAX_DAILY_TRADES, price, ladder, LADDER_DOWN_STEP_PCT * 100))
                return
        # �� v23: ֹ��/�����Ծ���Ϊ׼
        legs = st['long_legs']; avg_bp = self._leg_avg_price(legs) if legs else bp
        if avg_bp > 0 and price <= avg_bp * (1.0 - cfg.STOP_LOSS_PCT):
            _log('[FWD-T stop-loss trig] avg Y{:.2f} now Y{:.2f}({:.1f}%)'.format(avg_bp, price, (price - avg_bp) / avg_bp * 100))
            self._do_bt_force_sell(); return
        if price >= target:
            st['fstate'] = STATE_BT_SPIKING; st['bt_sell_peak_price'] = price
            _log('[FWD-T sellback watch] +{:.2f}% �� Y{:.2f}'.format((price - avg_bp) / avg_bp * 100, price))

    def _handle_bt_spiking(self, price):
        st = self.st
        if price > st.get('bt_sell_peak_price', price): st['bt_sell_peak_price'] = price
        peak = st.get('bt_sell_peak_price', price); pullback = (peak - price) / peak if peak > 0 else 0
        if pullback >= cfg.PULLBACK_PCT:
            # �� v23: ����ë�� = ��(���� - �������) �� ���ȹ���
            legs = st['long_legs'] or [(st.get('bt_buy_fill_price', price), TRADE_LOT_SIZE)]
            total_shares = self._leg_shares(legs)
            gross = sum((price - p) * s for p, s in legs)
            _log('[FWD-T sell trig] peak Y{:.2f} pullback {:.2f}% �� Y{:.2f} gross~Y{:,.0f}'.format(
                peak, pullback * 100, price, gross))
            status, delta = self._submit_order(-total_shares, price, 'FWD-T sell')
            if status in ('SKIP', 'TIMEOUT'):
                _log('[FWD-T sell FAIL] δ�ɽ�, �� BT_BOUGHT')
                st['fstate'] = STATE_BT_BOUGHT
                return
            sold = -delta
            if sold >= total_shares:
                st['long_legs'] = []; st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
                st['fstate'] = STATE_DONE
                self.total_t_days += 1; self.total_pnl += gross
                self._maybe_resume_trading()
            else:
                remaining = total_shares - sold
                st['long_legs'] = [(st.get('bt_buy_fill_price', price), remaining)]
                st['ladder_buy_target'] = 0.0
                st['fstate'] = STATE_BT_BOUGHT
                _log('[FWD-T sell PARTIAL] ���� {} sh, ʣ�� {} sh'.format(sold, remaining))

    def _do_bt_force_sell(self):
        _log('[FWD-T force sell trig]')
        st = self.st
        shares = self._leg_shares(st['long_legs']) or TRADE_LOT_SIZE
        price = self._cur_price()
        self._submit_order(-shares, price, 'FWD-T force sell')
        st['long_legs'] = []; st['ladder_buy_target'] = 0.0; st['ladder_bought_count'] = 0
        st['fstate'] = STATE_FORCED

    def _maybe_resume_trading(self):
        st = self.st
        tc_s = st.get('trade_count_short', 0); tc_l = st.get('trade_count_long', 0)
        do_short = st.get('do_short', False); do_long = st.get('do_long', False)
        can_s = do_short and tc_s < cfg.MAX_DAILY_TRADES
        can_l = do_long and tc_l < cfg.MAX_DAILY_TRADES
        block_reason = self._new_leg_block_reason()
        if (can_s or can_l) and not block_reason:
            # �� v28: ��T��ɺ�, �� sell_trigger ������ʵ��������
            #   ��������������ʾ�ѳɽ����ľ���ֵ (�� Y380.74 �ɽ����Է�������)
            sfp = st.get('sell_fill_price', 0)
            if sfp > 0:
                sig = st.get('daily_signal', {})
                old_trig = sig.get('sell_trigger', 0)
                if sfp > old_trig:
                    sig['sell_trigger'] = sfp
                    _log('[SELL-TRIG UPD] Y{:.2f}��Y{:.2f} (������, ������ʵ��������)'.format(old_trig, sfp))
            self._refresh_position()
            st['fstate'] = STATE_IDLE; st['peak_price'] = 0.0; st['dip_price'] = 0.0
            st['sell_fill_price'] = 0.0; st['buyback_target'] = 0.0
            st['rebound_99_armed'] = False
            # �� v22: ��ս���״̬
            st['short_legs'] = []; st['long_legs'] = []
            st['ladder_sell_target'] = 0.0; st['ladder_buy_target'] = 0.0
            st['ladder_sold_count'] = 0; st['ladder_bought_count'] = 0
            st['state_enter_time'] = cfg.now_hms(); st['stop_loss_hit'] = False
            parts = []
            if can_s: parts.append('REV-T {}/{}'.format(tc_s, cfg.MAX_DAILY_TRADES))
            if can_l: parts.append('FWD-T {}/{}'.format(tc_l, cfg.MAX_DAILY_TRADES))
            _log('[RESUME] �� IDLE ({})'.format(', '.join(parts)))
        elif block_reason and (can_s or can_l):
            _log('[RESUME BLOCKED] {}'.format(block_reason))
        else:
            _log('[DONE] {}/{} trades at limit'.format(tc_s + tc_l, cfg.MAX_DAILY_TRADES * 2))

    def _assess_strength(self, price, now_ts):
        st = self.st; sig = st.get('daily_signal', {}); open_price = sig.get('open_price', 0)
        if open_price <= 0: return
        st['price_history'].append((now_ts, price))
        cutoff = now_ts - cfg.LOCK_LOOKBACK_SEC
        st['price_history'] = [(t, p) for t, p in st['price_history'] if t >= cutoff]
        history = st['price_history']
        if len(history) < 10: return
        prices = [p for _, p in history]; p5 = prices[0]; pn = prices[-1]
        cond1 = pn > open_price * (1.0 + cfg.LOCK_PRICE_RATIO)
        cond2 = (pn - p5) / p5 > cfg.LOCK_MOMENTUM_PCT if p5 > 0 else False
        dh = max(prices); cond3 = (dh - pn) / dh < cfg.LOCK_DRAWDOWN_PCT if dh > 0 else False
        should_lock = cond1 and cond2 and cond3
        cool_ok = now_ts >= st.get('lock_cooldown_until', 0)
        if should_lock and not st.get('locked'):
            st['locked'] = True; st['lock_since'] = cfg.now_hms()
            st['lock_reason'] = 'P+{:.1f}% M {:.2f}% D {:.2f}%'.format(
                (pn / open_price - 1) * 100, (pn - p5) / p5 * 100, (dh - pn) / dh * 100 if dh > 0 else 0)
            _log('[LOCK] {}'.format(st['lock_reason']))
        elif not should_lock and st.get('locked') and cool_ok:
            st['locked'] = False; st['lock_reason'] = ''; st['lock_since'] = ''; _log('[UNLOCK]')
        if not should_lock and not st.get('locked'): st['lock_cooldown_until'] = 0.0
        if should_lock: st['lock_cooldown_until'] = 0.0
        elif st.get('locked') and st['lock_cooldown_until'] == 0.0:
            st['lock_cooldown_until'] = now_ts + cfg.LOCK_COOLDOWN_SEC

    # �T�T�T v25/v26/v33: ���߶�����ת���� (2��������ӦATR + REV��Ȩ + �����س�) �T�T�T

    def _mom_update_history(self, price, now_ts):
        """ά��2���Ӽ�ⴰ�� + 10����ATR���ڡ�"""
        hist = self.st['mom_price_history']
        hist.append((now_ts, price))
        cutoff = now_ts - MOM_WINDOW_SEC
        while hist and hist[0][0] < cutoff:
            hist.popleft()
        # �� v26: 10����ATR���� (������2���Ӽ�ⴰ��)
        atr_hist = self.st['mom_atr_history']
        atr_hist.append((now_ts, price))
        atr_cutoff = now_ts - MOM_ATR_WINDOW_SEC
        while atr_hist and atr_hist[0][0] < atr_cutoff:
            atr_hist.popleft()

    def _mom_compute_atr(self):
        """�������10����ATR (����: ��1����Ͱ�ڸߵ�����֮��λ��)��

        ����λ�����Ǿ�ֵ, ���޳�����ӱ߽������"��Ͱ"��������š�
        """
        hist = self.st['mom_atr_history']
        if len(hist) < 2:
            return 0.0
        buckets = {}
        for ts, px in hist:
            b = int(ts // 60)          # ��1���ӷ�Ͱ
            rec = buckets.get(b)
            if rec is None:
                buckets[b] = [px, px]
            else:
                if px > rec[0]: rec[0] = px
                if px < rec[1]: rec[1] = px
        ranges = sorted(h - l for h, l in buckets.values())
        if not ranges:
            return 0.0
        n = len(ranges)
        mid = n // 2
        if n % 2 == 1:
            return ranges[mid]
        return (ranges[mid - 1] + ranges[mid]) / 2.0

    def _mom_pullback_threshold(self, reference_price):
        """����MOM��߻���ȷ����ֵ���رտ���ʱ����v32��ȫ����ֵ��"""
        if not MOM_ADAPTIVE_PULLBACK_ENABLED:
            return cfg.PULLBACK_PCT
        frozen = self.st.get('mom_pullback_pct', 0.0)
        if frozen > 0:
            return frozen
        atr = self._mom_compute_atr()
        raw = MOM_PULLBACK_ATR_MULT * atr / reference_price \
            if reference_price > 0 and atr > 0 else MOM_PULLBACK_MIN_PCT
        return min(max(raw, MOM_PULLBACK_MIN_PCT), MOM_PULLBACK_MAX_PCT)

    def _mom_should_yield_to_rev(self, price):
        """MOM���������REV-T������ʱ��Ȩ������ͬһ���ǲ����ظ�������"""
        if not MOM_REV_PRIORITY_ENABLED or not self.st.get('do_short', False):
            return False
        signal = self.st.get('daily_signal') or {}
        rev_trigger = float(signal.get('sell_trigger', 0.0) or 0.0)
        if rev_trigger <= 0 or price <= 0:
            return False
        # �� v34: �ﵽREV-Tʵ��������ֵ������Ȩ��MOM�ɼ���������⡣
        if price >= rev_trigger:
            return False
        priority_floor = rev_trigger * (1.0 - MOM_REV_PRIORITY_BAND_PCT)
        main_state = self.st.get('fstate', STATE_IDLE)
        main_owns_wave = main_state in (STATE_SPIKING, STATE_SOLD, STATE_DIPPING)
        if price < priority_floor and not main_owns_wave:
            return False
        self.st['mom_rev_yield_trigger'] = rev_trigger
        reason = 'main {}'.format(main_state) if main_owns_wave else 'entered priority zone'
        _log('[MOM yield REV-T] Y{:.2f} | priority Y{:.2f} REV trig Y{:.2f} band {:.2f}% | {}'.format(
            price, priority_floor, rev_trigger, MOM_REV_PRIORITY_BAND_PCT * 100, reason))
        return True

    def _mom_handle_rev_yield(self, price):
        """������Ȩ���ﵽREV��ֵ�����ͷţ������˳�������������REV-T���к��ͷš�"""
        rev_trigger = self.st.get('mom_rev_yield_trigger', 0.0)
        priority_floor = rev_trigger * (1.0 - MOM_REV_PRIORITY_BAND_PCT)
        main_busy = self.st.get('fstate', STATE_IDLE) != STATE_IDLE
        reached_rev_trigger = rev_trigger > 0 and price >= rev_trigger
        exited_below = price < priority_floor and not main_busy
        if rev_trigger <= 0 or reached_rev_trigger or exited_below:
            self.st['mom_state'] = 'MOM_IDLE'
            self.st['mom_rev_yield_trigger'] = 0.0
            self.st['mom_peak'] = 0.0
            self.st['mom_pullback_pct'] = 0.0
            reason = 'reached REV trigger' if reached_rev_trigger else 'exited priority zone'
            _log('[MOM yield END] Y{:.2f} {}'.format(price, reason))

    def _mom_detect(self, price):
        """���2��������/���Ƿ񳬹�����Ӧ��ֵ������ 'UP' / 'DOWN' / None��"""
        trig = self.st.get('mom_trigger_pct', 0.0)
        if trig <= 0:
            return None
        hist = self.st['mom_price_history']
        if len(hist) < 2:
            return None
        base = hist[0][1]          # ����������� (Լ2����ǰ)
        if base <= 0 or price <= 0:
            return None
        chg = (price - base) / base
        if chg >= trig:
            return 'UP'
        if chg <= -trig:
            return 'DOWN'
        return None

    def _mom_tick(self, price, now_ts):
        """���߶������������ (ÿtick����, �������Ʋ���)��"""
        self._mom_update_history(price, now_ts)
        # �� v26: ����Ӧ�������� = 2 �� ���10����ATR
        #   ����"2���ӻ�׼��"���ǵ�ǰ��, �� _mom_detect ���ǵ���ͬ��׼, �����Բο�ƫ��
        atr = self._mom_compute_atr()
        hist = self.st['mom_price_history']
        base = hist[0][1] if hist else 0.0
        if base > 0 and atr > 0:
            trig = MOM_ATR_MULT * atr / base
        else:
            trig = MOM_TRIGGER_MIN_PCT    # ���ݲ���/ƽ���� �� ������, ������������
        trig = min(max(trig, MOM_TRIGGER_MIN_PCT), MOM_TRIGGER_MAX_PCT)
        self.st['mom_trigger_pct'] = trig
        ms = self.st.get('mom_state', 'MOM_IDLE')
        if ms == 'MOM_IDLE':
            self._mom_handle_idle(price)
        elif ms == 'MOM_SPIKING':
            self._mom_handle_spiking(price)
        elif ms == 'MOM_SOLD':
            self._mom_handle_sold(price)
        elif ms == 'MOM_DIPPING':
            self._mom_handle_dipping(price, now_ts)
        elif ms == 'MOM_BT_DIPPING':
            self._mom_handle_bt_dipping(price)
        elif ms == 'MOM_BT_BOUGHT':
            self._mom_handle_bt_bought(price)
        elif ms == 'MOM_BT_SPIKING':
            self._mom_handle_bt_spiking(price, now_ts)
        elif ms == MOM_STATE_BUYBACK_COOLING:
            self._mom_handle_buyback_cooling(price, now_ts)
        elif ms == MOM_STATE_SELLBACK_COOLING:
            self._mom_handle_sellback_cooling(price, now_ts)
        elif ms == MOM_STATE_REV_YIELD:
            self._mom_handle_rev_yield(price)
        # β��ǿ��ƽ�������� (���߲���ҹ)
        if cfg.now_hms() >= cfg.FORCE_CLOSE_TIME:
            self._mom_force_close(price)
        # ����: �ǿ���ʱÿ60s��ӡһ�ζ���״̬
        if ms != 'MOM_IDLE' and now_ts - self.st.get('mom_last_hb', 0) >= 60:
            self.st['mom_last_hb'] = now_ts
            self._mom_heartbeat(price)

    def _mom_status(self):
        """���ض��߻���״̬ժҪ��"""
        st = self.st; ms = st.get('mom_state', 'MOM_IDLE')
        if ms == 'MOM_IDLE':
            return ''
        if ms == 'MOM_SPIKING':
            return '��߻�������: peak Y{:.2f}'.format(st['mom_peak'])
        if ms == 'MOM_SOLD':
            return '����Y{:.2f} �ȵ�{:.1f}%���'.format(st['mom_sell_price'], MOM_SHORT_BUYBACK_PCT * 100)
        if ms == 'MOM_DIPPING':
            return '̽�׻������: dip Y{:.2f}'.format(st['mom_dip'])
        if ms == MOM_STATE_BUYBACK_COOLING:
            return '�����ȴ: trigger Y{:.2f} cycle {} / {:.0f}s'.format(
                st.get('mom_cooldown_trigger', 0.0), st.get('mom_cooldown_cycles', 0),
                st.get('mom_cooldown_duration', 0.0))
        if ms == 'MOM_BT_DIPPING':
            return '̽�׻�������: dip Y{:.2f}'.format(st['mom_dip'])
        if ms == 'MOM_BT_BOUGHT':
            return '����Y{:.2f} ����{:.1f}%����'.format(st['mom_buy_price'], MOM_LONG_SELLBACK_PCT * 100)
        if ms == 'MOM_BT_SPIKING':
            return '��߻�������: peak Y{:.2f}'.format(st['mom_peak'])
        if ms == MOM_STATE_SELLBACK_COOLING:
            return '������ȴ: trigger Y{:.2f} cycle {} / {:.0f}s'.format(
                st.get('mom_cooldown_trigger', 0.0), st.get('mom_cooldown_cycles', 0),
                st.get('mom_cooldown_duration', 0.0))
        if ms == MOM_STATE_REV_YIELD:
            return '����ȨREV-T trig Y{:.2f}'.format(st.get('mom_rev_yield_trigger', 0.0))
        return ms

    def _mom_heartbeat(self, price):
        trig = self.st.get('mom_trigger_pct', 0.0) * 100
        _log('[MOM-HB] {} Y{:.2f} trig��{:.2f}%'.format(self._mom_status(), price, trig))

    def _mom_handle_idle(self, price):
        st = self.st
        # β�̲��ٿ�����
        if cfg.now_hms() >= cfg.FORCE_CLOSE_TIME:
            return
        if st.get('mom_trade_count', 0) >= MOM_MAX_DAILY_TRADES:
            return
        sig = self._mom_detect(price)
        trig = st.get('mom_trigger_pct', 0.0) * 100
        if sig is None:
            return
        block_reason = self._new_leg_block_reason()
        if block_reason:
            self._mom_log_block(block_reason)
            return
        if sig == 'UP':
            self._refresh_position()
            capacity = calculate_execution_capacity(
                st.get('base_can_use', 0), self._available_cash(), price, 1)
            if not capacity['can_short']:
                self._mom_log_block('MOM short: {}'.format(
                    capacity['short_reason']))
                return
            st['mom_last_block_reason'] = ''
            st['mom_state'] = 'MOM_SPIKING'; st['mom_peak'] = price
            st['mom_pullback_pct'] = self._mom_pullback_threshold(price)
            _log('[MOM spike] 2min +{:.2f}% ({:.1f}��ATR10m {:.2f}%) Y{:.2f} �� ��߻���������� | pullback {:.2f}%{}'.format(
                trig, MOM_ATR_MULT, trig, price, st['mom_pullback_pct'] * 100,
                ' adaptive' if MOM_ADAPTIVE_PULLBACK_ENABLED else ' legacy'))
        elif sig == 'DOWN':
            capacity = self._paired_long_capacity(price)
            if not capacity['can_long']:
                self._mom_log_block('MOM long: {}'.format(
                    capacity['long_reason']))
                return
            st['mom_last_block_reason'] = ''
            st['mom_state'] = 'MOM_BT_DIPPING'; st['mom_dip'] = price
            _log('[MOM dip] 2min -{:.2f}% ({:.1f}��ATR10m {:.2f}%) Y{:.2f} �� ̽�׻���������'.format(
                trig, MOM_ATR_MULT, trig, price))

    def _mom_handle_spiking(self, price):
        """MOM��T��߻��������������س�ȷ�ϣ�����REV-T��������Ȩ��"""
        st = self.st
        if price > st['mom_peak']:
            st['mom_peak'] = price
        peak = st['mom_peak']
        if self._mom_should_yield_to_rev(price):
            st['mom_state'] = MOM_STATE_REV_YIELD
            st['mom_peak'] = 0.0
            st['mom_pullback_pct'] = 0.0
            return
        pullback = (peak - price) / peak if peak > 0 else 0
        pullback_threshold = self._mom_pullback_threshold(peak)
        if pullback >= pullback_threshold:
            block_reason = self._new_leg_block_reason()
            if block_reason:
                self._mom_log_block(
                    'MOM short arm canceled: {}'.format(block_reason))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_peak'] = 0.0
                st['mom_pullback_pct'] = 0.0
                return
            _log('[MOM sell trig] peak Y{:.2f} ����{:.2f}% >= {:.2f}% �� Y{:.2f}'.format(
                peak, pullback * 100, pullback_threshold * 100, price))
            status, delta = self._submit_order(-MOM_LOT_SIZE, price, 'MOM short')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[MOM short TIMEOUT] δ�ɽ�, �� IDLE')
                st['mom_state'] = 'MOM_IDLE'; st['mom_peak'] = 0.0; st['mom_pullback_pct'] = 0.0
                return
            st['mom_sell_price'] = price
            st['mom_leg_shares'] = abs(delta)
            st['mom_trade_count'] = st.get('mom_trade_count', 0) + 1
            st['mom_state'] = 'MOM_SOLD'
            st['mom_pullback_pct'] = 0.0
            _log('[MOM sold] Y{:.2f} x {} sh | ��ش��� ��Y{:.2f}'.format(
                price, st['mom_leg_shares'], round(price * (1 - MOM_SHORT_BUYBACK_PCT), 2)))

    def _mom_handle_sold(self, price):
        """������: ��1.2%����̽�׻������; (��ѡ)����3%����ֹ����ء�"""
        st = self.st
        sp = st['mom_sell_price']
        if sp <= 0:
            st['mom_state'] = 'MOM_IDLE'; return
        # �� v27: ����ֹ����ؿ��� �� �۸��ǳ�����3%ʱǿ����� (ǿţ������ʱ��Խ��Խ��)
        if MOM_EMERGENCY_BUYBACK_ENABLED and price >= sp * (1.0 + cfg.EMERGENCY_BUYBACK_PCT):
            _log('[MOM EMERG buyback] Y{:.2f}��Y{:.2f}(+{:.2f}%) ֹ�����'.format(
                sp, price, (price - sp) / sp * 100))
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _, delta = self._submit_buyback_order(shares, price, 'MOM emrg buyback')
            if delta > 0:
                buyback_price = getattr(self, '_last_buyback_price', price)
                gross = (sp - buyback_price) * delta
                self.total_t_days += 1; self.total_pnl += gross
                _log('[MOM short done(EMERG)] ��Y{:.2f} ��Y{:.2f} gross~Y{:,.0f}'.format(
                    sp, buyback_price, gross))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_sell_price'] = 0.0; st['mom_leg_shares'] = 0
            return
        # ������ش���: ��1.2% �� ̽�׻������
        if price <= sp * (1.0 - MOM_SHORT_BUYBACK_PCT):
            st['mom_state'] = 'MOM_DIPPING'; st['mom_dip'] = price
            _log('[MOM buyback trig] Y{:.2f} ��Y{:.2f}(-{:.2f}%) �� ̽�׻������'.format(
                price, round(sp * (1 - MOM_SHORT_BUYBACK_PCT), 2), MOM_SHORT_BUYBACK_PCT * 100))

    def _mom_start_close_cooldown(self, state, trigger, price, now_ts, label):
        """����/����MOMƽ����ȴ��ֻ��¼��ֹʱ�䣬������������ѭ����"""
        st = self.st
        now = _time.time() if now_ts is None else now_ts
        cycle = st.get('mom_cooldown_cycles', 0) + 1
        duration = max(
            MOM_CLOSE_COOLDOWN_MIN_SEC,
            MOM_CLOSE_COOLDOWN_START_SEC - MOM_CLOSE_COOLDOWN_STEP_SEC * (cycle - 1))
        st['mom_state'] = state
        st['mom_cooldown_trigger'] = round(trigger, 2)
        st['mom_cooldown_cycles'] = cycle
        st['mom_cooldown_duration'] = duration
        st['mom_cooldown_until'] = now + duration
        _log('[MOM {} cooldown #{}] Y{:.2f}, freeze {:.0f}s until {:.3f}'.format(
            label, cycle, price, duration, st['mom_cooldown_until']))

    def _mom_clear_close_cooldown(self):
        self.st['mom_cooldown_until'] = 0.0
        self.st['mom_cooldown_trigger'] = 0.0
        self.st['mom_cooldown_cycles'] = 0
        self.st['mom_cooldown_duration'] = 0.0

    def _mom_handle_dipping(self, price, now_ts=None):
        """̽�׻���׼����أ��ﵽ����ȷ�Ϻ�������ֵݼ��ķ�������ȴ��"""
        st = self.st
        if price < st['mom_dip']:
            st['mom_dip'] = price
        dip = st['mom_dip'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            trigger = st['mom_sell_price'] * (1.0 - MOM_SHORT_BUYBACK_PCT)
            _log('[MOM buyback ready] low Y{:.2f} ����{:.2f}% �� Y{:.2f}'.format(
                dip, bounce * 100, price))
            self._mom_start_close_cooldown(
                MOM_STATE_BUYBACK_COOLING, trigger, price, now_ts, 'buyback')

    def _mom_handle_buyback_cooling(self, price, now_ts=None):
        """��ȴ���ں󣬽�����ش�����������-0.5%֮����أ�����������"""
        st = self.st
        now = _time.time() if now_ts is None else now_ts
        if now < st.get('mom_cooldown_until', 0.0):
            return
        sp = st.get('mom_sell_price', 0.0)
        trigger = st.get('mom_cooldown_trigger', 0.0) or round(
            sp * (1.0 - MOM_SHORT_BUYBACK_PCT), 2)
        profit_ceiling = round(sp * (1.0 - MOM_CLOSE_MIN_PROFIT_PCT), 2)
        if price > trigger and price < profit_ceiling:
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM buyback cooldown PASS] Y{:.2f} > trig Y{:.2f} and < sell-0.5% Y{:.2f}'.format(
                price, trigger, profit_ceiling))
            _, delta = self._submit_buyback_order(shares, price, 'MOM buyback')
            if delta <= 0:
                _log('[MOM buyback FAIL] δ�ɽ�, �����ݼ���ȴ')
                self._mom_start_close_cooldown(
                    MOM_STATE_BUYBACK_COOLING, trigger, price, now, 'buyback')
                return
            buyback_price = getattr(self, '_last_buyback_price', price)
            gross = (sp - buyback_price) * delta
            self.total_t_days += 1; self.total_pnl += gross
            _log('[MOM short done] ��Y{:.2f} ��Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                sp, buyback_price, delta, gross))
            st['mom_state'] = 'MOM_IDLE'
            st['mom_sell_price'] = 0.0; st['mom_dip'] = 0.0; st['mom_leg_shares'] = 0
            self._mom_clear_close_cooldown()
            return
        reason = ('price still below/equal buyback trigger' if price <= trigger
                  else 'price reached/exceeded sell-0.5% ceiling')
        _log('[MOM buyback cooldown EXTEND] Y{:.2f}: {}, trigger Y{:.2f}, ceiling Y{:.2f}'.format(
            price, reason, trigger, profit_ceiling))
        self._mom_start_close_cooldown(
            MOM_STATE_BUYBACK_COOLING, trigger, price, now, 'buyback')

    def _mom_handle_bt_dipping(self, price):
        """̽�׻�������: ���ٹ�ֵ, ����BOUNCE_PCT������1�֡�"""
        st = self.st
        if price < st['mom_dip']:
            st['mom_dip'] = price
        dip = st['mom_dip'] or price
        bounce = (price - dip) / dip if dip > 0 else 0
        if bounce >= cfg.BOUNCE_PCT:
            block_reason = self._new_leg_block_reason()
            if block_reason:
                self._mom_log_block(
                    'MOM long arm canceled: {}'.format(block_reason))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_dip'] = 0.0
                return
            capacity = self._paired_long_capacity(price)
            if not capacity['can_long']:
                self._mom_log_block('MOM long recheck: {}'.format(
                    capacity['long_reason']))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_dip'] = 0.0
                return
            _log('[MOM buy trig] low Y{:.2f} ����{:.2f}% �� Y{:.2f}'.format(dip, bounce * 100, price))
            status, delta = self._submit_order(MOM_LOT_SIZE, price, 'MOM long')
            if status in ('SKIP', 'TIMEOUT'):
                if status == 'TIMEOUT':
                    _log('[MOM long TIMEOUT] δ�ɽ�, �� IDLE')
                st['mom_state'] = 'MOM_IDLE'; st['mom_dip'] = 0.0
                return
            st['mom_buy_price'] = price
            st['mom_leg_shares'] = abs(delta)
            st['mom_trade_count'] = st.get('mom_trade_count', 0) + 1
            st['mom_state'] = 'MOM_BT_BOUGHT'
            _log('[MOM bought] Y{:.2f} x {} sh | ���ش��� ��Y{:.2f}'.format(
                price, st['mom_leg_shares'], round(price * (1 + MOM_LONG_SELLBACK_PCT), 2)))

    def _mom_handle_bt_bought(self, price):
        """�����: ��1.5%�����߻�������; ����1.5%ֹ��������"""
        st = self.st
        bp = st['mom_buy_price']
        if bp <= 0:
            st['mom_state'] = 'MOM_IDLE'; return
        # ֹ������: �۸񷴵������1.5%
        if price <= bp * (1.0 - cfg.STOP_LOSS_PCT):
            _log('[MOM stop-loss] Y{:.2f}��Y{:.2f}(-{:.2f}%) ֹ������'.format(
                bp, price, (bp - price) / bp * 100))
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _, delta = self._submit_order(-shares, price, 'MOM stop-loss')
            if delta < 0:
                gross = (price - bp) * (-delta)
                self.total_t_days += 1; self.total_pnl += gross
                _log('[MOM long done(stop)] ��Y{:.2f} ��Y{:.2f} gross~Y{:,.0f}'.format(bp, price, gross))
                st['mom_state'] = 'MOM_IDLE'
                st['mom_buy_price'] = 0.0; st['mom_leg_shares'] = 0
            return
        # �������ش���: ��1.5% �� ��߻�������
        if price >= bp * (1.0 + MOM_LONG_SELLBACK_PCT):
            st['mom_state'] = 'MOM_BT_SPIKING'; st['mom_peak'] = price
            st['mom_pullback_pct'] = self._mom_pullback_threshold(price)
            _log('[MOM sellback trig] Y{:.2f} ��Y{:.2f}(+{:.2f}%) �� ��߻������� | pullback {:.2f}%{}'.format(
                price, round(bp * (1 + MOM_LONG_SELLBACK_PCT), 2), MOM_LONG_SELLBACK_PCT * 100,
                st['mom_pullback_pct'] * 100,
                ' adaptive' if MOM_ADAPTIVE_PULLBACK_ENABLED else ' legacy'))

    def _mom_handle_bt_spiking(self, price, now_ts=None):
        """MOM��T���أ��س�ȷ�Ϻ�������ֵݼ��ķ�������ȴ��"""
        st = self.st
        if price > st['mom_peak']:
            st['mom_peak'] = price
        peak = st['mom_peak']
        pullback = (peak - price) / peak if peak > 0 else 0
        pullback_threshold = self._mom_pullback_threshold(peak)
        if pullback >= pullback_threshold:
            trigger = st['mom_buy_price'] * (1.0 + MOM_LONG_SELLBACK_PCT)
            _log('[MOM sellback ready] peak Y{:.2f} ����{:.2f}% >= {:.2f}% �� Y{:.2f}'.format(
                peak, pullback * 100, pullback_threshold * 100, price))
            self._mom_start_close_cooldown(
                MOM_STATE_SELLBACK_COOLING, trigger, price, now_ts, 'sellback')

    def _mom_handle_sellback_cooling(self, price, now_ts=None):
        """��ȴ���ں󣬽������+0.5%�����ش�����֮������������������"""
        st = self.st
        now = _time.time() if now_ts is None else now_ts
        if now < st.get('mom_cooldown_until', 0.0):
            return
        bp = st.get('mom_buy_price', 0.0)
        trigger = st.get('mom_cooldown_trigger', 0.0) or round(
            bp * (1.0 + MOM_LONG_SELLBACK_PCT), 2)
        profit_floor = round(bp * (1.0 + MOM_CLOSE_MIN_PROFIT_PCT), 2)
        if price < trigger and price > profit_floor:
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM sellback cooldown PASS] Y{:.2f} < trig Y{:.2f} and > buy+0.5% Y{:.2f}'.format(
                price, trigger, profit_floor))
            _, delta = self._submit_order(-shares, price, 'MOM sellback')
            sold = -delta
            if sold <= 0:
                _log('[MOM sellback FAIL] δ�ɽ�, �����ݼ���ȴ')
                self._mom_start_close_cooldown(
                    MOM_STATE_SELLBACK_COOLING, trigger, price, now, 'sellback')
                return
            gross = (price - bp) * sold
            self.total_t_days += 1; self.total_pnl += gross
            _log('[MOM long done] ��Y{:.2f} ��Y{:.2f} x {}sh gross~Y{:,.0f}'.format(
                bp, price, sold, gross))
            st['mom_state'] = 'MOM_IDLE'
            st['mom_buy_price'] = 0.0; st['mom_peak'] = 0.0; st['mom_leg_shares'] = 0
            st['mom_pullback_pct'] = 0.0
            self._mom_clear_close_cooldown()
            return
        reason = ('price still above/equal sellback trigger' if price >= trigger
                  else 'price fell to/below buy+0.5% floor')
        _log('[MOM sellback cooldown EXTEND] Y{:.2f}: {}, trigger Y{:.2f}, floor Y{:.2f}'.format(
            price, reason, trigger, profit_floor))
        self._mom_start_close_cooldown(
            MOM_STATE_SELLBACK_COOLING, trigger, price, now, 'sellback')

    def _mom_force_close(self, price):
        """β��ǿ��ƽ�������� (��������� / ���������), ���߲���ҹ��"""
        st = self.st
        ms = st.get('mom_state')
        if ms in ('MOM_SOLD', MOM_STATE_BUYBACK_COOLING):
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM force buyback] Y{:.2f}'.format(price))
            _, delta = self._submit_buyback_order(shares, price, 'MOM force buyback')
            if delta <= 0:
                _log('[WARN] MOM force buyback δ�ɽ�, �����ȿ��ܲ���!')
            st['mom_state'] = 'MOM_IDLE'; st['mom_sell_price'] = 0.0; st['mom_leg_shares'] = 0
        elif ms in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING', MOM_STATE_SELLBACK_COOLING):
            shares = st.get('mom_leg_shares', 0) or MOM_LOT_SIZE
            _log('[MOM force sell] Y{:.2f}'.format(price))
            _, delta = self._submit_order(-shares, price, 'MOM force sell')
            if delta >= 0:
                _log('[WARN] MOM force sell δ�ɽ�, �����ȿ��ܲ���!')
            st['mom_state'] = 'MOM_IDLE'; st['mom_buy_price'] = 0.0; st['mom_leg_shares'] = 0
        elif ms in ('MOM_SPIKING', 'MOM_DIPPING', 'MOM_BT_DIPPING', MOM_STATE_REV_YIELD):
            # ��δ����, ֱ�Ӹ�λ
            st['mom_state'] = 'MOM_IDLE'; st['mom_peak'] = 0.0; st['mom_dip'] = 0.0
            st['mom_pullback_pct'] = 0.0; st['mom_rev_yield_trigger'] = 0.0
        self._mom_clear_close_cooldown()

    # �T�T�T ��ѭ�� �T�T�T

    def run(self):
        set_global_conn(self.conn, self.dry_run)
        if not self.dry_run:
            if not self.conn.connect_data(): _log('[ERROR] market data connect failed'); return
            if not self.conn.connect_trade(): _log('[ERROR] trade connect failed'); self.conn.disconnect(); return
        else:
            if not self.conn.connect_data(): _log('[ERROR] market data connect failed'); return
        self._init_state()
        _log('[START] {} v39 {} {}'.format(STOCK_NAME, 'LIVE' if not self.dry_run else 'SIGNAL', STOCK_QMT))

        try:
            self._daily_init()
            signal = self.st.get('daily_signal')
            if signal: self._print_daily_brief(signal)
        except Exception as e:
            self._lock_all_trading('daily init exception: {}'.format(e))
            _log('[ERROR] init failed: {}'.format(e)); _traceback.print_exc()

        try:
            while self._running:
                now = cfg.now_hms(); now_ts = _time.time()
                if not cfg.is_market_open(now):
                    today = datetime.now().strftime('%Y%m%d')
                    if '09:30:00' <= now < '09:30:59':
                        if self.st.get('_pre_market_done', '') != today:
                            _log('[PRE-MKT {}] computing signal...'.format(now))
                            try:
                                self._daily_init(); self.st['_pre_market_done'] = today
                                signal = self.st.get('daily_signal')
                                if signal: self._print_daily_brief(signal)
                            except Exception as e:
                                self._lock_all_trading(
                                    'daily init exception: {}'.format(e))
                                _log('[PRE-MKT ERROR] {}'.format(e))
                            self._last_heartbeat = now_ts; _time.sleep(5); continue
                        if now_ts - self._last_heartbeat >= 60:
                            self._last_heartbeat = now_ts
                            _log('[PRE-MKT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        _time.sleep(5); continue
                    if self.st.get('trade_date', '') != today:
                        try:
                            self._daily_init()
                            signal = self.st.get('daily_signal')
                            if signal: self._print_daily_brief(signal)
                        except Exception as e:
                            self._lock_all_trading(
                                'daily init exception: {}'.format(e))
                            _log('[ERROR] init failed: {}'.format(e))
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        if now < '09:30:00': _log('[WAIT {}] to open {}'.format(now, cfg.time_to_open(now)))
                        elif now > '15:00:00': _log('[CLOSE {}]'.format(now))
                        elif '11:30:00' < now < '13:00:00': _log('[LUNCH {}]'.format(now))
                    _time.sleep(10); continue

                fstate = self.st.get('fstate', STATE_IDLE)
                if fstate in (STATE_DONE, STATE_FORCED):
                    tick = self.ctx.get_full_tick([STOCK_QMT])
                    tick_data = tick.get(STOCK_QMT, {})
                    price = tick_data.get('lastPrice', 0)
                    self._update_limit_up_guard(price, tick_data, now_ts)
                    # �� v25: ���߶�����������������β(DONE/FORCED)ʱ�Զ�������
                    if MOM_ENABLED and price > 0:
                        self._mom_tick(price, now_ts)
                    if now_ts - self._last_heartbeat >= 30:
                        self._last_heartbeat = now_ts
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        _log('[STATE] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                            fstate, price, tc_s, cfg.MAX_DAILY_TRADES,
                            tc_l, cfg.MAX_DAILY_TRADES, self.total_t_days, self.total_pnl))
                    if now < '14:57:00':
                        tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
                        if ((self.st.get('do_short', False) and tc_s < cfg.MAX_DAILY_TRADES) or
                            (self.st.get('do_long', False) and tc_l < cfg.MAX_DAILY_TRADES)):
                            self._maybe_resume_trading()
                    _time.sleep(3); continue

                tick = self.ctx.get_full_tick([STOCK_QMT])
                if STOCK_QMT not in tick: _time.sleep(1); continue
                tick_data = tick[STOCK_QMT]
                price = tick_data.get('lastPrice', 0)
                if price <= 0: _time.sleep(1); continue
                self._update_limit_up_guard(price, tick_data, now_ts)
                # �� v25: ���߶������� (�����������ź�/��״̬��, ÿtick����)
                if MOM_ENABLED:
                    self._mom_tick(price, now_ts)
                # �� v21: �����׸���Чtick��ӡ����ȷ��
                if not self.st.get('_market_open_logged', True):
                    self.st['_market_open_logged'] = True
                    sig_chk = self.st.get('daily_signal', {})
                    # �� v28: �ý��տ��̼����� sell_trigger (��ǰ��������������տ��̼�)
                    _open_now = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {}).get('open', 0)
                    _open_old = sig_chk.get('open_price', 0)
                    if (_open_now > 0 and
                            (_open_old <= 0 or abs(_open_now - _open_old) >= 0.005)):
                        _sm = sig_chk.get('sell_mult', 0.40)
                        _atr = sig_chk.get('atr_pct', 0.03)
                        _new_raw = _open_now * (
                            1.0 + _atr * _sm * cfg.SELL_TRIGGER_SCALE)
                        _range_ma = sig_chk.get('daily_range_ma10', 0.0)
                        _range_cap = _open_now * (
                            1.0 + _range_ma * cfg.DAILY_RANGE_CAP_MULT)
                        _range_capped = (
                            cfg.DAILY_RANGE_CAP_ENABLED and _new_raw > _range_cap)
                        _new_trig = round(
                            _range_cap if _range_capped else _new_raw, 2)
                        _old_trig = sig_chk.get('sell_trigger', 0)
                        sig_chk['sell_trigger'] = _new_trig
                        sig_chk['sell_trigger_raw'] = round(_new_raw, 2)
                        sig_chk['range_capped'] = _range_capped
                        sig_chk['open_price'] = _open_now
                        _buy_floor = round(
                            _open_now * (1.0 - cfg.BUY_TRIGGER_PCT), 2)
                        _buy_trail = round(
                            price * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
                        sig_chk['buy_trigger_floor'] = _buy_floor
                        sig_chk['buy_trigger_trail'] = _buy_trail
                        sig_chk['buy_trigger'] = max(_buy_floor, _buy_trail)
                        sig_chk['sellback_target_hint'] = round(
                            sig_chk['buy_trigger'] *
                            (1.0 + cfg.SELLBACK_RISE_PCT), 2)
                        _log('[SELL-TRIG RECALC] open Y{:.2f}��Y{:.2f} trig Y{:.2f}��Y{:.2f} (Mult {:.2f} ATR {:.1f}%)'.format(
                            _open_old, _open_now, _old_trig, _new_trig, _sm, _atr * 100))
                    st_trig = sig_chk.get('sell_trigger', 0)
                    bt_trig = sig_chk.get('buy_trigger', 0)
                    bits = ['OPEN', 'Y{:.2f}'.format(price)]
                    if st_trig > 0:
                        if price >= st_trig:
                            bits.append('REV-T exceeded by Y{:.2f}'.format(
                                price - st_trig))
                        else:
                            bits.append('REV-T needs +{:.2f}% to Y{:.2f}'.format(
                                (st_trig - price) / price * 100, st_trig))
                    if bt_trig > 0:
                        if price <= bt_trig:
                            bits.append('FWD-T threshold reached')
                        else:
                            bits.append('FWD-T needs -{:.2f}% to Y{:.2f}'.format(
                                (price - bt_trig) / price * 100, bt_trig))
                    if self.st.get('locked'): bits.append('LOCKED')
                    if self.st.get('limit_up_guard'): bits.append('LIMIT-UP-GUARD')
                    _log('[{}]'.format('] ['.join(bits)))
                signal = self.st.get('daily_signal')
                do_short = self.st.get('do_short', False); do_long = self.st.get('do_long', False)
                if signal is None or (not do_short and not do_long):
                    if now_ts - self._last_heartbeat >= 300:
                        self._last_heartbeat = now_ts
                        _log('[STANDBY] Y{:.2f} no trade direction'.format(price))
                    _time.sleep(5); continue
                if fstate == STATE_IDLE: self._assess_strength(price, now_ts)
                if fstate == STATE_IDLE: self._handle_idle(price)
                elif fstate == STATE_SPIKING: self._handle_spiking(price)
                elif fstate == STATE_SOLD: self._handle_sold(price)
                elif fstate == STATE_DIPPING: self._handle_dipping(price)
                elif fstate == STATE_BT_DIPPING: self._handle_bt_dipping(price)
                elif fstate == STATE_BT_BOUGHT: self._handle_bt_bought(price)
                elif fstate == STATE_BT_SPIKING: self._handle_bt_spiking(price)
                if self.st['fstate'] in (STATE_SOLD, STATE_DIPPING): self.st['sell_elapsed_bars'] += 1
                if cfg.ENABLE_FORCE_CLOSE and now >= cfg.FORCE_CLOSE_TIME:
                    f = self.st['fstate']
                    if f in (STATE_SOLD, STATE_DIPPING): self._force_buyback()
                    elif f in (STATE_BT_BOUGHT, STATE_BT_SPIKING): self._do_bt_force_sell()
                    elif f in (STATE_SPIKING, STATE_BT_DIPPING, STATE_IDLE): self.st['fstate'] = STATE_DONE
                if fstate == STATE_SOLD and not self.st.get('stop_loss_hit', False):
                    loss_limit = self.st['base_shares'] * signal['open_price'] * cfg.STOP_LOSS_PCT
                    if self.st.get('day_pnl', 0) < -loss_limit:
                        _log('[STOP-LOSS] REV-T loss over limit'); self.st['stop_loss_hit'] = True; self._force_buyback()
                if now_ts - self._last_heartbeat >= 60:
                    self._last_heartbeat = now_ts; self._heartbeat(price)
                _time.sleep(0.5)
        except KeyboardInterrupt: _log('Interrupted by user')
        except Exception as e: _log('[ERROR] {}'.format(e)); _traceback.print_exc()
        finally:
            self.conn.disconnect()
            if self.st.get('fstate', '') in (STATE_SOLD, STATE_DIPPING): _log('[WARN] position not bought back!')
            # �� v25: �����Ȳ������� (v29: MOM_ENABLED=Falseʱ����)
            if MOM_ENABLED:
                mom_ms = self.st.get('mom_state', '')
                if mom_ms in ('MOM_SOLD', 'MOM_DIPPING', MOM_STATE_BUYBACK_COOLING):
                    _log('[WARN] MOM short leg not bought back!')
                elif mom_ms in ('MOM_BT_BOUGHT', 'MOM_BT_SPIKING', MOM_STATE_SELLBACK_COOLING):
                    _log('[WARN] MOM long leg not sold!')
            _log('[STOP] {} v39 cum {} days gross~Y{:,.0f}'.format(STOCK_NAME, self.total_t_days, self.total_pnl))
            logger = get_logger()
            if logger is not None: logger.close()

    def _heartbeat(self, price):
        fs = self.st['fstate']; sig = self.st.get('daily_signal', {})
        if fs in (STATE_DONE, STATE_FORCED):
            tc_s = self.st.get('trade_count_short', 0); tc_l = self.st.get('trade_count_long', 0)
            _log('[HB] {} Y{:.2f} REV-T {}/{} FWD-T {}/{} cum {} trades~Y{:,.0f}'.format(
                fs, price, tc_s, cfg.MAX_DAILY_TRADES, tc_l, cfg.MAX_DAILY_TRADES,
                self.total_t_days, self.total_pnl)); return
        if fs == STATE_IDLE:
            guard_active = self.st.get('limit_up_guard', False)
            if self.st.get('do_long') and not guard_active:
                bt_floor = sig.get('buy_trigger_floor', 0)
                bt_trail = round(price * (1.0 - cfg.BUY_TRIGGER_TRAIL), 2)
                self.st['bt_max_trail'] = max(self.st.get('bt_max_trail', 0), bt_trail)
                sig['buy_trigger'] = max(bt_floor, self.st['bt_max_trail'])
                sig['buy_trigger_trail'] = bt_trail
            parts = []
            if self.st.get('do_short'):
                st_trig = sig.get('sell_trigger', 0)
                if price >= st_trig:
                    parts.append('REV-T: exceeded Y{:.2f} by Y{:.2f}'.format(
                        st_trig, price - st_trig))
                else:
                    parts.append('REV-T: needs +Y{:.2f} to Y{:.2f}'.format(
                        st_trig - price, st_trig))
            else:
                parts.append('REV-T: off ({})'.format(
                    sig.get('short_reason', 'not executable')))
            if self.st.get('do_long'):
                bt_dyn = sig.get('buy_trigger', 0)
                if price <= bt_dyn:
                    parts.append('FWD-T: threshold reached Y{:.2f}'.format(
                        bt_dyn))
                else:
                    parts.append('FWD-T: needs -Y{:.2f} to Y{:.2f}'.format(
                        price - bt_dyn, bt_dyn))
            else:
                parts.append('FWD-T: off ({})'.format(
                    self.st.get('long_reason', 'not executable')))
            if self.st.get('locked'): parts.append('LOCKED')
            if guard_active: parts.append('LIMIT-UP-GUARD')
            _log('[HB] {} Y{:.2f} {}'.format(fs, price, ' | '.join(parts)))
        elif fs == STATE_SPIKING:
            peak = self.st.get('peak_price', 0); pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}%'.format(fs, price, peak, pb))
        elif fs in (STATE_SOLD, STATE_DIPPING):
            sp = self.st.get('sell_fill_price', 0); bt = self.st.get('buyback_target', 0)
            ladder = self.st.get('ladder_sell_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if sp > 0: _log('[HB] {} Y{:.2f} sell Y{:.2f} {:+.1f}% buyback Y{:.2f}{}'.format(
                fs, price, sp, (price - sp) / sp * 100, bt, extra))
        elif fs == STATE_BT_DIPPING:
            dip = self.st.get('bt_dip_price', price); bounce = (price - dip) / dip * 100 if dip > 0 else 0
            _log('[HB] {} Y{:.2f} dip Y{:.2f} bounce {:.2f}%'.format(fs, price, dip, bounce))
        elif fs == STATE_BT_BOUGHT:
            bp = self.st.get('bt_buy_fill_price', 0); target = self.st.get('bt_sellback_target', 0)
            ladder = self.st.get('ladder_buy_target', 0)
            extra = ' ladder Y{:.2f}'.format(ladder) if ladder > 0 else ''
            if bp > 0: _log('[HB] {} Y{:.2f} buy Y{:.2f} {:+.1f}% sellback Y{:.2f}{}'.format(
                fs, price, bp, (price - bp) / bp * 100, target, extra))
        elif fs == STATE_BT_SPIKING:
            peak = self.st.get('bt_sell_peak_price', price); pb = (peak - price) / peak * 100 if peak > 0 else 0
            _log('[HB] {} Y{:.2f} peak Y{:.2f} pullback {:.2f}%'.format(fs, price, peak, pb))
        else: _log('[HB] {} Y{:.2f}'.format(fs, price))


def run_backtest_mode(start='20250801', end='20260806'):
    print('=' * 55 + '\n  Backtest QMT mini REV-T v39\n  Range: {} ~ {}\n'.format(start, end) + '=' * 55)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from backtest.backtest_v10_xtdata import XTDataManager, BacktestEngine
    data_mgr = XTDataManager('601869.SH', data_dir='C:/QMT/datadir')
    data_mgr.load_daily(start=start, end=end)
    engine = BacktestEngine(data_mgr); engine.run(start_date=start, end_date=end)
    engine.print_report(); engine.save_csv()


def main():
    parser = argparse.ArgumentParser(description='MiniQMT internal daily Trading v39 �� MOM��ȴʱ����εݼ�')
    parser.add_argument('--mode', '-m', default='signal', choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801'); parser.add_argument('--end', default='20260806')
    args = parser.parse_args()
    if args.mode == 'backtest': run_backtest_mode(args.start, args.end); return
    logger = FileLogger(STOCK_CODE, version='v39'); set_logger(logger)
    dry_run = (args.mode == 'signal')
    if args.mode == 'live':
        print('\n!!! LIVE TRADING CONFIRMATION !!!\nTarget: {}({}) Account: {}'.format(STOCK_NAME, STOCK_CODE, ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes': print('Cancelled'); logger.close(); return
    StrategyRunner(dry_run=dry_run).run()


if __name__ == '__main__':
    main()
