# -*- coding: gbk -*-
"""MiniQMT day trading v41: v40 plus a 10-minute MA position report.

MA5 and MA20 use the current intraday price as today's close, together with
the previous 4 or 19 completed daily closes.  This keeps the reported moving
averages aligned with the values visible during the trading session.
"""

import argparse
import math
import time as _time

import DayTradeing_v40_stragety_miniqmt as v40
from DayTradeing_v40_stragety_miniqmt import *  # noqa: F401,F403


MA_REPORT_INTERVAL_SEC = 600.0
MA20_RISK_RATIO = 0.97


def calculate_ma_position(completed_closes, current_price):
    """Return intraday MA position data, or None when data is insufficient."""
    price = float(current_price or 0.0)
    closes = [float(value) for value in completed_closes
              if math.isfinite(float(value)) and float(value) > 0]
    if price <= 0 or len(closes) < 19:
        return None

    ma5 = (sum(closes[-4:]) + price) / 5.0
    ma20 = (sum(closes[-19:]) + price) / 20.0
    return {
        'ma5': ma5,
        'ma20': ma20,
        'ma5_position': 'ABOVE' if price > ma5 else (
            'BELOW' if price < ma5 else 'AT'),
        'ma20_position': 'ABOVE' if price > ma20 else (
            'BELOW' if price < ma20 else 'AT'),
        'ma5_gap_pct': (price / ma5 - 1.0) * 100.0,
        'ma20_gap_pct': (price / ma20 - 1.0) * 100.0,
        'ma20_risk_price': ma20 * MA20_RISK_RATIO,
        'risk': price < ma20 * MA20_RISK_RATIO,
    }


class StrategyRunner(v40.StrategyRunner):
    def _init_state(self):
        super()._init_state()
        self.st['ma_completed_closes'] = []
        self.st['ma_history_date'] = ''
        self.st['last_ma_report_time'] = 0.0

    def _daily_init(self):
        super()._daily_init()
        trade_date = self.st.get('trade_date', '')
        if (not self.st.get('initialized', False) or
                self.st.get('ma_history_date', '') == trade_date):
            return

        tick_data = self.ctx.get_full_tick([STOCK_QMT]).get(STOCK_QMT, {})
        snapshot = self.conn.load_daily_snapshot(
            v40.cfg.HIST_DATA_LEN, today=trade_date,
            tick_last_close=float(tick_data.get('lastClose', 0) or 0),
            retries=3, retry_delay=1.0)
        if snapshot is None:
            return
        closes = snapshot['adjusted']['close'].astype(float).tolist()
        self.st['ma_completed_closes'] = closes[-19:]
        self.st['ma_history_date'] = trade_date

    def _update_intraday_average(self, tick_data):
        super()._update_intraday_average(tick_data)
        self._maybe_report_ma(
            float(tick_data.get('lastPrice', 0) or 0), _time.time())

    def _maybe_report_ma(self, price, now_ts):
        last_report = float(self.st.get('last_ma_report_time', 0.0) or 0.0)
        if now_ts - last_report < MA_REPORT_INTERVAL_SEC:
            return False

        position = calculate_ma_position(
            self.st.get('ma_completed_closes', []), price)
        if position is None:
            return False

        self.st['last_ma_report_time'] = now_ts
        v40._log(
            '[MA-POS] price Y{:.2f} | MA5 Y{:.2f}: {} {:+.2f}% | '
            'MA20 Y{:.2f}: {} {:+.2f}%'.format(
                price, position['ma5'], position['ma5_position'],
                position['ma5_gap_pct'], position['ma20'],
                position['ma20_position'], position['ma20_gap_pct']))
        if position['risk']:
            v40._log(
                '[MA20 RISK] price Y{:.2f} is below 97% of MA20 '
                '(risk line Y{:.2f})'.format(
                    price, position['ma20_risk_price']))
        return True


def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT internal daily Trading v41 - 10-minute MA report')
    parser.add_argument(
        '--mode', '-m', default='signal',
        choices=['signal', 'live', 'backtest'])
    parser.add_argument('--start', default='20250801')
    parser.add_argument('--end', default='20260806')
    args = parser.parse_args()
    if args.mode == 'backtest':
        v40.run_backtest_mode(args.start, args.end)
        return

    logger = v40.FileLogger(STOCK_CODE, version='v41')
    v40.set_logger(logger)
    dry_run = args.mode == 'signal'
    if args.mode == 'live':
        print('\n!!! LIVE TRADING CONFIRMATION !!!\nTarget: {}({}) Account: {}'.format(
            STOCK_NAME, STOCK_CODE, ACCOUNT))
        if input('Type yes to continue: ').strip().lower() != 'yes':
            print('Cancelled')
            logger.close()
            return
    StrategyRunner(dry_run=dry_run).run()


if __name__ == '__main__':
    main()
