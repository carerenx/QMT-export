# -*- coding: utf-8 -*-
"""Backtest v12 with screening and entry separated by one trading session."""

import csv
import math
import os

import backtest_csi500_overnight_v10 as bt10
import backtest_csi500_overnight_v11 as bt11
import backtest_csi500_overnight_v12 as bt12
import CSI500_MA5_MA20_Overnight_v14_miniqmt as strategy


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')


def simulate_delayed_trade(code, screen_date, buy_date, sell_date,
                           entry_bars, exit_bars, index_bars, index_closes,
                           blocked_times):
    if len(entry_bars) < 2:
        return None
    day_open = float(entry_bars[0][1])
    first_time = entry_bars[0][0]
    first_price = float(entry_bars[0][4])
    watch = strategy.v11.new_buy_watch(day_open, first_price, first_time)
    previous_price = first_price
    index_by_time = {row[0]: row[1:] for row in index_bars}
    buy_price = None
    buy_time = None

    for time_text, open_price, high, low, close in entry_bars[1:]:
        if time_text in blocked_times:
            previous_price = float(close)
            continue
        for point_index, raw_price in enumerate((open_price, high, low, close)):
            price = float(raw_price)
            index_price = bt11._market_price(
                index_by_time, time_text, point_index)
            if (not math.isfinite(price) or price <= 0 or
                    index_price is None or
                    not strategy.v11.is_market_allowed(
                        index_closes, index_price)):
                previous_price = price
                continue
            event = strategy.v11.advance_buy_watch(watch, price, time_text)
            if event == 'BUY':
                trigger = float(watch['dip_price']) * (
                    1.0 + strategy.v11.BUY_BOUNCE_PCT)
                raw_fill = bt11._continuous_fill(
                    previous_price, price, trigger, 'UP')
                buy_price = bt11._slipped(raw_fill, 'BUY')
                buy_time = time_text
                break
            previous_price = price
        if buy_price is not None:
            break
    if buy_price is None:
        return None

    trade = bt12._exit_without_stop(
        code, buy_date, sell_date, buy_time, buy_price, exit_bars)
    if trade:
        trade['screen_date'] = screen_date
    return trade


def _collect_opportunities(xtdata, candidates, trading_dates, index_history):
    opportunities = []
    date_position = {date: index for index, date in enumerate(trading_dates)}
    screen_dates = [
        date for date in sorted(candidates)
        if date_position.get(date, len(trading_dates)) + 2 < len(trading_dates)]
    for sequence, screen_date in enumerate(screen_dates, 1):
        position = date_position[screen_date]
        buy_date = trading_dates[position + 1]
        sell_date = trading_dates[position + 2]
        pairs = candidates[screen_date]
        breadth_pairs = candidates.get(buy_date, [])
        codes = sorted(set(
            [code for code, _ in pairs] + [code for code, _ in breadth_pairs]))
        request_codes = codes + [strategy.v11.INDEX_CODE]
        minute_data = bt10._read_minute_range(
            xtdata, request_codes, buy_date, sell_date)
        missing = [
            code for code in request_codes
            if not bt10._minute_data_complete(
                minute_data.get(code), buy_date, sell_date)]
        if missing:
            print('[MINUTE {}] downloading {}'.format(
                buy_date, len(missing)), flush=True)
            bt10._download_minute_with_timeout(missing, buy_date, sell_date)
            minute_data = bt10._read_minute_range(
                xtdata, request_codes, buy_date, sell_date)

        index_closes = index_history.get(buy_date, [])
        index_bars = bt10._minute_rows(
            minute_data.get(strategy.v11.INDEX_CODE), buy_date)
        if len(index_closes) < 25 or not index_bars:
            print('[SIM {}/{}] {} skipped: index data'.format(
                sequence, len(screen_dates), screen_date), flush=True)
            continue
        blocked = bt11._screen_times(
            buy_date, breadth_pairs, minute_data, index_bars, index_closes)
        completed = 0
        for code, _ in pairs:
            entry_bars = bt10._minute_rows(minute_data.get(code), buy_date)
            exit_bars = [
                row for row in bt10._minute_rows(minute_data.get(code), sell_date)
                if row[0] <= '10:00:00']
            trade = simulate_delayed_trade(
                code, screen_date, buy_date, sell_date, entry_bars, exit_bars,
                index_bars, index_closes, blocked)
            if trade:
                opportunities.append(trade)
                completed += 1
        print(
            '[SIM {}/{}] screen={} buy={} raw={} blocked={} trades={} total={}'.format(
                sequence, len(screen_dates), screen_date, buy_date, len(pairs),
                len(blocked), completed, len(opportunities)), flush=True)
    return opportunities


def _write_outputs(start, end, trades, metrics, opportunities):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v14_delayed_entry_{}_{}'.format(start, end)
    csv_path = os.path.join(OUTPUT_DIR, prefix + '_trades.csv')
    report_path = os.path.join(OUTPUT_DIR, prefix + '_report.md')
    fields = [
        'code', 'name', 'screen_date', 'buy_date', 'buy_time', 'buy_price',
        'sell_date', 'sell_time', 'sell_price', 'shares', 'exit_reason',
        'buy_commission', 'sell_commission', 'stamp_tax', 'net_pnl',
        'return_pct']
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(trades)
    reasons = ', '.join('{}={}'.format(key, value)
                        for key, value in sorted(metrics['exit_reasons'].items()))
    lines = [
        '# 中证500 MA5/MA20 隔夜策略 v14 延迟入场回测', '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 筛选日D：只记录候选，不进行买入',
        '- 买入日D+1：使用v12原买入机制，未买入的候选当日结束后过期',
        '- 卖出日D+2：上涨1.2%后回落0.1%，否则10:00强制卖出',
        '- 市场过滤、仓位、成本及无1.5%止损设置与v12相同', '',
        '## 结果', '',
        '- 独立机会数：{}'.format(opportunities),
        '- 资金约束后交易数：{}'.format(metrics['trades']),
        '- 胜/负：{}/{}'.format(metrics['wins'], metrics['losses']),
        '- 胜率：{:.2f}%'.format(metrics['win_rate']),
        '- 净利润：{:,.2f}元'.format(metrics['net_pnl']),
        '- 组合收益率：{:.2f}%'.format(metrics['return_pct']),
        '- 平均每笔：{:,.2f}元'.format(metrics['average_pnl']),
        '- Profit Factor：{:.3f}'.format(metrics['profit_factor']),
        '- 最大已实现权益回撤：{:.2f}%'.format(
            metrics['max_realized_drawdown_pct']),
        '- 最大占用资金：{:,.2f}元'.format(metrics['max_capital_used']),
        '- 卖出原因：{}'.format(reasons or '-'), '',
        '## 限制', '',
        '- 使用2026-08-31成分股快照，存在幸存者偏差。',
        '- 分钟K线不能完全还原逐笔成交顺序和盘口流动性。',
        '- 最大回撤基于已实现权益，不包含持仓期间浮动盈亏。',
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
    index_history = bt11._index_history(xtdata, start, end)
    print('[CANDIDATES] potential stock-days={}'.format(
        sum(len(value) for value in candidates.values())))
    opportunities = _collect_opportunities(
        xtdata, candidates, trading_dates, index_history)
    trades, final_cash, max_capital_used = bt11._allocate_portfolio(
        opportunities)
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
