# -*- coding: utf-8 -*-
"""One-year minute backtest for the risk-filtered v11 strategy."""

import csv
import heapq
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta

import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v10 as bt10
import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v11_miniqmt as strategy


INITIAL_CAPITAL = 1000000.0
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.0
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.0005
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')


def calculate_trade_costs(buy_price, sell_price, shares):
    buy_amount = float(buy_price) * int(shares)
    sell_amount = float(sell_price) * int(shares)
    buy_commission = max(MIN_COMMISSION, buy_amount * COMMISSION_RATE)
    sell_commission = max(MIN_COMMISSION, sell_amount * COMMISSION_RATE)
    stamp_tax = sell_amount * STAMP_TAX_RATE
    return {
        'buy_commission': round(buy_commission, 6),
        'sell_commission': round(sell_commission, 6),
        'stamp_tax': round(stamp_tax, 6),
        'buy_total': buy_amount + buy_commission,
        'sell_net': sell_amount - sell_commission - stamp_tax,
        'net_pnl': round(
            sell_amount - buy_amount - buy_commission - sell_commission - stamp_tax,
            6),
    }


def _slipped(price, side):
    multiplier = 1.0 + SLIPPAGE_RATE if side == 'BUY' else 1.0 - SLIPPAGE_RATE
    return round(float(price) * multiplier, 4)


def _market_price(index_by_time, time_text, point_index):
    values = index_by_time.get(time_text)
    if not values:
        return None
    return float(values[min(point_index, len(values) - 1)])


def _screen_times(entry_date, pairs, minute_data, index_bars, index_closes):
    index_by_time = {row[0]: row[1:] for row in index_bars}
    counts = defaultdict(int)
    for code, previous in pairs:
        frame = minute_data.get(code)
        if frame is None:
            continue
        for time_text, _, _, _, close in bt10._minute_rows(frame, entry_date):
            index_price = _market_price(index_by_time, time_text, 3)
            if index_price is None:
                continue
            if (strategy.is_market_allowed(index_closes, index_price) and
                    strategy.is_screen_match(previous, float(close))):
                counts[time_text] += 1
    return set(time_text for time_text, count in counts.items()
               if count > strategy.MAX_REALTIME_MATCHES)


def _continuous_fill(previous_price, current_price, threshold, side):
    if previous_price is None:
        return current_price
    if side == 'UP' and previous_price < threshold <= current_price:
        return threshold
    if side == 'DOWN' and previous_price > threshold >= current_price:
        return threshold
    return current_price


def simulate_candidate_trade(code, entry_date, exit_date, previous_closes,
                             entry_bars, exit_bars, index_bars, index_closes,
                             blocked_screen_times):
    index_by_time = {row[0]: row[1:] for row in index_bars}
    watch = None
    buy_price = None
    buy_time = None
    previous_price = None
    if not entry_bars:
        return None
    day_open = float(entry_bars[0][1])

    for time_text, open_price, high, low, close in entry_bars:
        points = (open_price, high, low, close)
        if watch is None:
            index_price = _market_price(index_by_time, time_text, 3)
            if (time_text not in blocked_screen_times and
                    index_price is not None and
                    strategy.is_market_allowed(index_closes, index_price) and
                    strategy.is_screen_match(previous_closes, float(close))):
                watch = strategy.new_buy_watch(day_open, float(close), time_text)
                previous_price = float(close)
            continue

        if time_text in blocked_screen_times:
            previous_price = float(close)
            continue

        for point_index, raw_price in enumerate(points):
            price = float(raw_price)
            index_price = _market_price(index_by_time, time_text, point_index)
            if (not math.isfinite(price) or price <= 0 or index_price is None or
                    not strategy.is_market_allowed(index_closes, index_price)):
                previous_price = price
                continue
            event = strategy.advance_buy_watch(watch, price, time_text)
            if event == 'BUY':
                trigger = float(watch['dip_price']) * (1.0 + strategy.BUY_BOUNCE_PCT)
                raw_fill = _continuous_fill(previous_price, price, trigger, 'UP')
                buy_price = _slipped(raw_fill, 'BUY')
                buy_time = time_text
                break
            previous_price = price
        if buy_price is not None:
            break
    if buy_price is None:
        return None

    phase = 'BOUGHT'
    peak = 0.0
    previous_price = None
    stop_price = buy_price * (1.0 - strategy.STOP_LOSS_PCT)
    target_price = buy_price * (1.0 + strategy.SELLBACK_RISE_PCT)
    for time_text, open_price, high, low, close in exit_bars:
        if time_text >= strategy.FORCE_SELL_TIME:
            return _trade(code, entry_date, exit_date, buy_time, buy_price,
                          time_text, _slipped(open_price, 'SELL'), 'FORCE_1000')
        for raw_price in (open_price, high, low, close):
            price = float(raw_price)
            if not math.isfinite(price) or price <= 0:
                continue
            if price <= stop_price:
                raw_fill = _continuous_fill(previous_price, price, stop_price, 'DOWN')
                return _trade(code, entry_date, exit_date, buy_time, buy_price,
                              time_text, _slipped(raw_fill, 'SELL'), 'STOP_LOSS')
            if phase == 'BOUGHT' and price >= target_price:
                phase = 'SPIKING'
                peak = price
            elif phase == 'SPIKING':
                peak = max(peak, price)
                pullback_price = peak * (1.0 - strategy.SELL_PULLBACK_PCT)
                if price <= pullback_price:
                    raw_fill = _continuous_fill(
                        previous_price, price, pullback_price, 'DOWN')
                    return _trade(code, entry_date, exit_date, buy_time, buy_price,
                                  time_text, _slipped(raw_fill, 'SELL'), 'SELLBACK')
            previous_price = price
    return None


def _trade(code, entry_date, exit_date, buy_time, buy_price,
           sell_time, sell_price, reason):
    return {
        'code': code,
        'buy_date': entry_date,
        'buy_time': buy_time,
        'buy_price': round(buy_price, 4),
        'sell_date': exit_date,
        'sell_time': sell_time,
        'sell_price': round(sell_price, 4),
        'exit_reason': reason,
        'shares': 100,
    }


def _index_history(xtdata, start, end):
    warmup = (datetime.strptime(start, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
    xtdata.download_history_data(strategy.INDEX_CODE, '1d', warmup, end)
    data = xtdata.get_local_data(
        field_list=bt10.MINUTE_FIELDS,
        stock_list=[strategy.INDEX_CODE],
        period='1d',
        start_time=warmup,
        end_time=end,
        dividend_type='front',
        fill_data=True,
    ) or {}
    rows = bt10._daily_rows(data.get(strategy.INDEX_CODE))
    closes = [row[4] for row in rows]
    result = {}
    for index, row in enumerate(rows):
        if index >= 25:
            result[row[0]] = closes[:index]
    return result


def _collect_opportunities(xtdata, candidates, trading_dates, index_history):
    opportunities = []
    date_position = {date: index for index, date in enumerate(trading_dates)}
    candidate_dates = [date for date in sorted(candidates)
                       if date_position.get(date, len(trading_dates)) + 1 < len(trading_dates)]
    for sequence, entry_date in enumerate(candidate_dates, 1):
        exit_date = trading_dates[date_position[entry_date] + 1]
        pairs = candidates[entry_date]
        codes = [code for code, _ in pairs]
        request_codes = codes + [strategy.INDEX_CODE]
        minute_data = bt10._read_minute_range(
            xtdata, request_codes, entry_date, exit_date)
        missing = [code for code in request_codes if not bt10._minute_data_complete(
            minute_data.get(code), entry_date, exit_date)]
        if missing:
            print('[MINUTE {}] downloading {}'.format(entry_date, len(missing)), flush=True)
            bt10._download_minute_with_timeout(missing, entry_date, exit_date)
            minute_data = bt10._read_minute_range(
                xtdata, request_codes, entry_date, exit_date)

        index_closes = index_history.get(entry_date, [])
        index_bars = bt10._minute_rows(
            minute_data.get(strategy.INDEX_CODE), entry_date)
        if len(index_closes) < 25 or not index_bars:
            print('[SIM {}/{}] {} skipped: index data'.format(
                sequence, len(candidate_dates), entry_date), flush=True)
            continue
        blocked = _screen_times(
            entry_date, pairs, minute_data, index_bars, index_closes)
        completed = 0
        for code, previous in pairs:
            entry_bars = bt10._minute_rows(minute_data.get(code), entry_date)
            exit_bars = [row for row in bt10._minute_rows(
                minute_data.get(code), exit_date) if row[0] <= '10:00:00']
            trade = simulate_candidate_trade(
                code, entry_date, exit_date, previous, entry_bars, exit_bars,
                index_bars, index_closes, blocked)
            if trade:
                opportunities.append(trade)
                completed += 1
        print('[SIM {}/{}] {} raw={} blocked_minutes={} trades={} total={}'.format(
            sequence, len(candidate_dates), entry_date, len(pairs), len(blocked),
            completed, len(opportunities)), flush=True)
    return opportunities


def _allocate_portfolio(opportunities):
    accepted = []
    active = []
    active_codes = set()
    daily_entries = defaultdict(int)
    cash = INITIAL_CAPITAL
    max_capital_used = 0.0
    serial = 0
    for trade in sorted(opportunities, key=lambda item: (
            item['buy_date'], item['buy_time'], item['code'])):
        entry_key = trade['buy_date'] + trade['buy_time'].replace(':', '')
        while active and active[0][0] <= entry_key:
            _, _, finished = heapq.heappop(active)
            cash += finished['sell_net']
            active_codes.discard(finished['code'])
        if trade['code'] in active_codes:
            continue
        if len(active) >= strategy.MAX_CONCURRENT_POSITIONS:
            continue
        if daily_entries[trade['buy_date']] >= strategy.MAX_DAILY_ENTRIES:
            continue
        max_position_value = INITIAL_CAPITAL * strategy.MAX_POSITION_PCT
        if trade['buy_price'] * trade['shares'] > max_position_value:
            continue
        costs = calculate_trade_costs(
            trade['buy_price'], trade['sell_price'], trade['shares'])
        if cash + 1e-9 < costs['buy_total']:
            continue
        trade.update(costs)
        trade['return_pct'] = costs['net_pnl'] / costs['buy_total'] * 100.0
        cash -= costs['buy_total']
        accepted.append(trade)
        active_codes.add(trade['code'])
        daily_entries[trade['buy_date']] += 1
        serial += 1
        exit_key = trade['sell_date'] + trade['sell_time'].replace(':', '')
        heapq.heappush(active, (exit_key, serial, trade))
        max_capital_used = max(max_capital_used, INITIAL_CAPITAL - cash)
    while active:
        _, _, finished = heapq.heappop(active)
        cash += finished['sell_net']
    return accepted, cash, max_capital_used


def _write_outputs(start, end, trades, metrics, opportunities):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v11_{}_{}'.format(start, end)
    csv_path = os.path.join(OUTPUT_DIR, prefix + '_trades.csv')
    report_path = os.path.join(OUTPUT_DIR, prefix + '_report.md')
    fields = [
        'code', 'name', 'buy_date', 'buy_time', 'buy_price', 'sell_date',
        'sell_time', 'sell_price', 'shares', 'exit_reason', 'buy_commission',
        'sell_commission', 'stamp_tax', 'net_pnl', 'return_pct']
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(trades)
    reasons = ', '.join('{}={}'.format(key, value)
                        for key, value in sorted(metrics['exit_reasons'].items()))
    lines = [
        '# 中证500 MA5/MA20 隔夜策略 v11 回测', '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 入场：指数趋势过滤、实时广度过滤、低点稳定3分钟并反弹0.3%',
        '- 风控：单股最多3%初始资金、最多8只持仓、每日最多10笔',
        '- 成本：佣金万2.5且最低5元、印花税千1、买卖各5BP滑点',
        '- 撮合：一分钟OHLC路径；阈值穿越按阈值成交，开盘跳空按开盘成交', '',
        '## 结果', '',
        '- 独立机会数：{}'.format(opportunities),
        '- 资金约束后交易数：{}'.format(metrics['trades']),
        '- 胜/负：{}/{}'.format(metrics['wins'], metrics['losses']),
        '- 胜率：{:.2f}%'.format(metrics['win_rate']),
        '- 净利润：{:,.2f}元'.format(metrics['net_pnl']),
        '- 组合收益率：{:.2f}%'.format(metrics['return_pct']),
        '- 平均每笔：{:,.2f}元'.format(metrics['average_pnl']),
        '- Profit Factor：{:.3f}'.format(metrics['profit_factor']),
        '- 最大已实现权益回撤：{:.2f}%'.format(metrics['max_realized_drawdown_pct']),
        '- 最大占用资金：{:,.2f}元'.format(metrics['max_capital_used']),
        '- 卖出原因：{}'.format(reasons or '-'), '',
        '## 限制', '',
        '- 使用2026-08-31成分股快照，存在幸存者偏差。',
        '- 分钟K线仍不能完全还原逐笔成交顺序和盘口流动性。',
        '- 最大回撤仍基于已实现权益，不包含持仓期间浮动盈亏。',
    ]
    with open(report_path, 'w', encoding='utf-8') as file:
        file.write('\n'.join(lines) + '\n')
    return report_path, csv_path


def run_backtest(start='20250903', end='20260902'):
    xtdata = bt10._connect_xtdata()
    codes = bt10.load_bundled_constituents()
    daily_data = bt10._load_daily_data(xtdata, codes, start, end)
    candidates, trading_dates, _ = bt10._build_candidates(
        daily_data, codes, start, end)
    index_history = _index_history(xtdata, start, end)
    print('[CANDIDATES] potential stock-days={}'.format(
        sum(len(value) for value in candidates.values())))
    opportunities = _collect_opportunities(
        xtdata, candidates, trading_dates, index_history)
    trades, final_cash, max_capital_used = _allocate_portfolio(opportunities)
    bt10._add_names(xtdata, trades)
    metrics = bt10._metrics(trades, final_cash, max_capital_used)
    report_path, csv_path = _write_outputs(
        start, end, trades, metrics, len(opportunities))
    print('[RESULT] trades={} win_rate={:.2f}% pnl={:,.2f} return={:.2f}%'.format(
        metrics['trades'], metrics['win_rate'], metrics['net_pnl'],
        metrics['return_pct']))
    print('[REPORT] {}'.format(report_path))
    print('[TRADES] {}'.format(csv_path))
    return 0


if __name__ == '__main__':
    raise SystemExit(run_backtest())
