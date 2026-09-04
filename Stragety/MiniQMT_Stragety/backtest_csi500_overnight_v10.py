# -*- coding: utf-8 -*-
"""One-year minute backtest for CSI500_MA5_MA20_Overnight_v10_miniqmt."""

import csv
import heapq
import math
import multiprocessing
import os
from collections import defaultdict
from datetime import datetime, timedelta

from CSI500_MA5_MA20_Overnight_v10_miniqmt import (
    MA5_BELOW_PERCENT,
    TRADE_LOT_SIZE,
    advance_buy_watch,
    advance_sell_watch,
    is_screen_match,
    new_buy_watch,
    new_sell_watch,
)
from CSI500_MA5_MA20_Screener_v4_miniqmt import load_bundled_constituents


INITIAL_CAPITAL = 1000000.0
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.001
MINUTE_FIELDS = ['open', 'high', 'low', 'close']
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')


def calculate_trade_costs(buy_price, sell_price, shares):
    buy_amount = float(buy_price) * int(shares)
    sell_amount = float(sell_price) * int(shares)
    buy_commission = buy_amount * COMMISSION_RATE
    sell_commission = sell_amount * COMMISSION_RATE
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


def simulate_candidate_trade(code, entry_date, exit_date, previous_closes,
                             entry_bars, exit_bars):
    watch = None
    buy_price = None
    buy_time = None
    if not entry_bars:
        return None
    day_open = float(entry_bars[0][1])
    for time_text, open_price, high, low, close in entry_bars:
        for price in (open_price, high, low, close):
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                continue
            if watch is None and is_screen_match(previous_closes, price):
                watch = new_buy_watch(day_open, price)
            if watch is not None and advance_buy_watch(watch, price) == 'BUY':
                buy_price = price
                buy_time = time_text
                break
        if buy_price is not None:
            break
    if buy_price is None:
        return None

    sell_state = new_sell_watch(entry_date, buy_price)
    for time_text, open_price, high, low, close in exit_bars:
        for price in (open_price, high, low, close):
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                continue
            event = advance_sell_watch(sell_state, exit_date, time_text, price)
            if event:
                return {
                    'code': code,
                    'buy_date': entry_date,
                    'buy_time': buy_time,
                    'buy_price': round(buy_price, 4),
                    'sell_date': exit_date,
                    'sell_time': time_text,
                    'sell_price': round(price, 4),
                    'exit_reason': event,
                    'shares': TRADE_LOT_SIZE,
                }
    return None


def _date_key(value):
    if hasattr(value, 'strftime'):
        return value.strftime('%Y%m%d')
    text = str(value).replace('-', '').replace('/', '')
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    try:
        stamp = float(value)
        if stamp > 100000000000:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp).strftime('%Y%m%d')
    except Exception:
        return ''


def _date_time_key(value):
    if hasattr(value, 'strftime'):
        return value.strftime('%Y%m%d'), value.strftime('%H:%M:%S')
    text = str(value).replace('-', '').replace('/', '').replace(':', '').replace(' ', '')
    if len(text) >= 14 and text[:14].isdigit():
        raw = text[:14]
        return raw[:8], '{}:{}:{}'.format(raw[8:10], raw[10:12], raw[12:14])
    try:
        stamp = float(value)
        if stamp > 100000000000:
            stamp /= 1000.0
        dt = datetime.fromtimestamp(stamp)
        return dt.strftime('%Y%m%d'), dt.strftime('%H:%M:%S')
    except Exception:
        return '', ''


def _valid_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _daily_rows(frame):
    rows = []
    if frame is None:
        return rows
    for index, row in frame.iterrows():
        date = _date_key(index)
        values = [_valid_number(row[field]) for field in MINUTE_FIELDS]
        if date and all(value is not None for value in values):
            rows.append((date,) + tuple(values))
    rows.sort(key=lambda item: item[0])
    return rows


def _minute_rows(frame, wanted_date):
    rows = []
    if frame is None:
        return rows
    for index, row in frame.iterrows():
        date, time_text = _date_time_key(index)
        if date != wanted_date:
            continue
        values = [_valid_number(row[field]) for field in MINUTE_FIELDS]
        if time_text and all(value is not None for value in values):
            rows.append((time_text,) + tuple(values))
    rows.sort(key=lambda item: item[0])
    return rows


def _potential_screen_day(previous_closes, low, high):
    if len(previous_closes) < 19:
        return False
    rate = MA5_BELOW_PERCENT / 100.0
    lower = sum(previous_closes[-19:]) / 19.0
    sum4 = sum(previous_closes[-4:])
    upper = (1.0 - rate) * sum4 / (5.0 - (1.0 - rate))
    return max(float(low), lower) < min(float(high), upper)


def _connect_xtdata():
    from xtquant import xtdata
    if hasattr(xtdata, 'enable_hello'):
        xtdata.enable_hello = False
    if hasattr(xtdata, 'connect'):
        xtdata.connect()
    else:
        client = xtdata.get_client()
        if not client.is_connected():
            raise RuntimeError('XtData is not connected')
    return xtdata


def _download_progress(prefix):
    last = [-1]

    def callback(data):
        finished = int(data.get('finished', 0) or 0)
        total = int(data.get('total', 0) or 0)
        if finished != last[0] and (finished == 1 or finished == total or finished % 25 == 0):
            print('{} {}/{}'.format(prefix, finished, total), flush=True)
            last[0] = finished
    return callback


def _download_minute_worker(codes, start_time, end_time):
    from xtquant import xtdata
    if hasattr(xtdata, 'enable_hello'):
        xtdata.enable_hello = False
    if hasattr(xtdata, 'connect'):
        xtdata.connect()
    else:
        xtdata.get_client()
    for code in codes:
        xtdata.download_history_data(code, '1m', start_time, end_time)


def _download_minute_with_timeout(codes, entry_date, exit_date):
    start_time = entry_date + '000000'
    end_time = exit_date + '100000'
    process = multiprocessing.Process(
        target=_download_minute_worker,
        args=(codes, start_time, end_time),
    )
    process.start()
    process.join(30)
    if process.is_alive():
        process.terminate()
        process.join()
        print('[MINUTE {}] batch timeout; retrying one by one'.format(
            entry_date), flush=True)
    else:
        return

    for index, code in enumerate(codes, 1):
        if index == 1 or index == len(codes) or index % 25 == 0:
            print('[MINUTE {}] retry {}/{} {}'.format(
                entry_date, index, len(codes), code), flush=True)
        process = multiprocessing.Process(
            target=_download_minute_worker,
            args=([code], start_time, end_time),
        )
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
            print('[MINUTE {}] skipped after timeout: {}'.format(
                entry_date, code), flush=True)


def _load_daily_data(xtdata, codes, start, end):
    warmup = (datetime.strptime(start, '%Y%m%d') - timedelta(days=70)).strftime('%Y%m%d')
    print('[DAILY] refreshing {} stocks {}~{}'.format(len(codes), warmup, end))
    xtdata.download_history_data2(
        codes, '1d', warmup, end, _download_progress('[DAILY]'))
    return xtdata.get_local_data(
        field_list=MINUTE_FIELDS,
        stock_list=codes,
        period='1d',
        start_time=warmup,
        end_time=end,
        dividend_type='front',
        fill_data=True,
    ) or {}


def _build_candidates(daily_data, codes, start, end):
    candidates = defaultdict(list)
    trading_dates = set()
    close_lookup = defaultdict(dict)
    for code in codes:
        rows = _daily_rows(daily_data.get(code))
        closes = [row[4] for row in rows]
        for index, row in enumerate(rows):
            date, open_price, high, low, close = row
            if start <= date <= end:
                trading_dates.add(date)
                close_lookup[date][code] = close
            if index < 19 or not start <= date <= end:
                continue
            previous = closes[index - 19:index]
            if _potential_screen_day(previous, low, high):
                candidates[date].append((code, previous))
    return candidates, sorted(trading_dates), close_lookup


def _read_minute_range(xtdata, codes, entry_date, exit_date):
    return xtdata.get_local_data(
        field_list=MINUTE_FIELDS,
        stock_list=codes,
        period='1m',
        start_time=entry_date + '000000',
        end_time=exit_date + '100000',
        dividend_type='front',
        fill_data=True,
    ) or {}


def _minute_data_complete(frame, entry_date, exit_date):
    entry_rows = _minute_rows(frame, entry_date)
    exit_rows = [row for row in _minute_rows(frame, exit_date)
                 if row[0] <= '10:00:00']
    return len(entry_rows) >= 200 and len(exit_rows) >= 25


def _collect_opportunities(xtdata, candidates, trading_dates):
    opportunities = []
    date_position = {date: index for index, date in enumerate(trading_dates)}
    candidate_dates = [date for date in sorted(candidates)
                       if date_position.get(date, len(trading_dates)) + 1 < len(trading_dates)]
    for sequence, entry_date in enumerate(candidate_dates, 1):
        exit_date = trading_dates[date_position[entry_date] + 1]
        pairs = candidates[entry_date]
        codes = [code for code, _ in pairs]
        minute_data = _read_minute_range(xtdata, codes, entry_date, exit_date)
        missing = [code for code in codes if not _minute_data_complete(
            minute_data.get(code), entry_date, exit_date)]
        if missing:
            print('[MINUTE {}] downloading {}'.format(
                entry_date, len(missing)), flush=True)
            _download_minute_with_timeout(missing, entry_date, exit_date)
            minute_data = _read_minute_range(
                xtdata, codes, entry_date, exit_date)

        completed = 0
        for code, previous in pairs:
            frame = minute_data.get(code)
            entry_bars = _minute_rows(frame, entry_date)
            exit_bars = [row for row in _minute_rows(frame, exit_date)
                         if row[0] <= '10:00:00']
            trade = simulate_candidate_trade(
                code, entry_date, exit_date, previous, entry_bars, exit_bars)
            if trade:
                opportunities.append(trade)
                completed += 1
        print('[SIM {}/{}] {} candidates={} trades={} total={}'.format(
            sequence, len(candidate_dates), entry_date, len(pairs),
            completed, len(opportunities)), flush=True)
    return opportunities


def _allocate_portfolio(opportunities):
    accepted = []
    active = []
    active_codes = set()
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
        costs = calculate_trade_costs(
            trade['buy_price'], trade['sell_price'], trade['shares'])
        if cash + 1e-9 < costs['buy_total']:
            continue
        trade.update(costs)
        trade['return_pct'] = costs['net_pnl'] / costs['buy_total'] * 100.0
        cash -= costs['buy_total']
        accepted.append(trade)
        active_codes.add(trade['code'])
        serial += 1
        exit_key = trade['sell_date'] + trade['sell_time'].replace(':', '')
        heapq.heappush(active, (exit_key, serial, trade))
        max_capital_used = max(max_capital_used, INITIAL_CAPITAL - cash)
    while active:
        _, _, finished = heapq.heappop(active)
        cash += finished['sell_net']
    return accepted, cash, max_capital_used


def _add_names(xtdata, trades):
    names = {}
    for code in sorted(set(trade['code'] for trade in trades)):
        try:
            detail = xtdata.get_instrument_detail(code) or {}
            names[code] = detail.get('InstrumentName') or '-'
        except Exception:
            names[code] = '-'
    for trade in trades:
        trade['name'] = names.get(trade['code'], '-')


def _metrics(trades, final_cash, max_capital_used):
    count = len(trades)
    wins = sum(1 for trade in trades if trade['net_pnl'] > 0)
    losses = sum(1 for trade in trades if trade['net_pnl'] < 0)
    total_pnl = sum(trade['net_pnl'] for trade in trades)
    gross_profit = sum(max(0.0, trade['net_pnl']) for trade in trades)
    gross_loss = -sum(min(0.0, trade['net_pnl']) for trade in trades)
    reasons = defaultdict(int)
    for trade in trades:
        reasons[trade['exit_reason']] += 1
    realized_equity = INITIAL_CAPITAL
    realized_peak = INITIAL_CAPITAL
    max_realized_drawdown = 0.0
    for trade in sorted(trades, key=lambda item: (
            item['sell_date'], item['sell_time'], item['code'])):
        realized_equity += trade['net_pnl']
        realized_peak = max(realized_peak, realized_equity)
        if realized_peak > 0:
            drawdown = (realized_peak - realized_equity) / realized_peak * 100.0
            max_realized_drawdown = max(max_realized_drawdown, drawdown)
    return {
        'trades': count,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / float(count) * 100.0 if count else 0.0,
        'net_pnl': total_pnl,
        'return_pct': (final_cash / INITIAL_CAPITAL - 1.0) * 100.0,
        'average_pnl': total_pnl / count if count else 0.0,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0.0,
        'max_realized_drawdown_pct': max_realized_drawdown,
        'max_capital_used': max_capital_used,
        'exit_reasons': dict(reasons),
    }


def _write_outputs(start, end, trades, metrics, opportunity_count):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v10_{}_{}'.format(start, end)
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

    reason_text = ', '.join('{}={}'.format(key, value)
                            for key, value in sorted(metrics['exit_reasons'].items()))
    lines = [
        '# 中证500 MA5/MA20 隔夜策略 v10 回测',
        '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 股票池：2026-08-31 中证500成分股快照（存在幸存者偏差）',
        '- 筛选：当前价低于MA5 {:.2f}%，且高于MA20'.format(MA5_BELOW_PERCENT),
        '- 买入：v40正T主机制，动态跌幅触发后从低点反弹0.1%',
        '- 卖出：下一交易日上涨1.2%后回落0.1%，或下跌1.5%止损；10:00强制卖出',
        '- 撮合：1分钟K线内按 Open→High→Low→Close；100股/笔',
        '- 初始资金：{:,.2f}元；佣金万2.5；卖出印花税千1；未计滑点及最低佣金'.format(INITIAL_CAPITAL),
        '',
        '## 结果',
        '',
        '- 独立机会数：{}'.format(opportunity_count),
        '- 资金约束后交易数：{}'.format(metrics['trades']),
        '- 胜/负：{}/{}'.format(metrics['wins'], metrics['losses']),
        '- 胜率：{:.2f}%'.format(metrics['win_rate']),
        '- 净利润：{:,.2f}元'.format(metrics['net_pnl']),
        '- 组合收益率：{:.2f}%'.format(metrics['return_pct']),
        '- 平均每笔：{:,.2f}元'.format(metrics['average_pnl']),
        '- 盈亏比（Profit Factor）：{:.3f}'.format(metrics['profit_factor']),
        '- 最大已实现权益回撤：{:.2f}%'.format(metrics['max_realized_drawdown_pct']),
        '- 最大占用资金：{:,.2f}元'.format(metrics['max_capital_used']),
        '- 卖出原因：{}'.format(reason_text or '-'),
        '',
        '## 限制',
        '',
        '- 使用当前成分股快照回看历史，未还原历史调入调出，存在幸存者偏差。',
        '- 分钟K线无法还原逐笔成交顺序，OHLC路径假设可能高估或低估触发。',
        '- 未模拟涨跌停无法成交、停牌延迟卖出、滑点和每笔最低5元佣金。',
    ]
    with open(report_path, 'w', encoding='utf-8') as file:
        file.write('\n'.join(lines) + '\n')
    return report_path, csv_path


def run_backtest(start='20250903', end='20260902'):
    xtdata = _connect_xtdata()
    codes = load_bundled_constituents()
    daily_data = _load_daily_data(xtdata, codes, start, end)
    candidates, trading_dates, close_lookup = _build_candidates(
        daily_data, codes, start, end)
    candidate_pairs = sum(len(value) for value in candidates.values())
    print('[CANDIDATES] potential stock-days={}'.format(candidate_pairs))
    opportunities = _collect_opportunities(xtdata, candidates, trading_dates)
    trades, final_cash, max_capital_used = _allocate_portfolio(opportunities)
    _add_names(xtdata, trades)
    metrics = _metrics(trades, final_cash, max_capital_used)
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
