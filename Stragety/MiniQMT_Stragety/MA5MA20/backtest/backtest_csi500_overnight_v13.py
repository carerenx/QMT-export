# -*- coding: utf-8 -*-
"""V12 backtest with an ATR14 percentage entry filter."""

import csv
import os
from collections import defaultdict

import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v10 as bt10
import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v11 as bt11
import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v12 as bt12
import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v13_miniqmt as strategy


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')


def filter_candidates_by_atr(daily_data, candidates):
    filtered = defaultdict(list)
    atr_values = {}
    candidate_dates_by_code = defaultdict(set)
    for date, pairs in candidates.items():
        for code, _ in pairs:
            candidate_dates_by_code[code].add(date)
    for code, frame in daily_data.items():
        rows = bt10._daily_rows(frame)
        compact = [(row[0], row[2], row[3], row[4]) for row in rows]
        by_date = {row[0]: index for index, row in enumerate(compact)}
        for date in candidate_dates_by_code.get(code, ()):
            index = by_date.get(date)
            if index is None:
                continue
            history = [(row[1], row[2], row[3]) for row in compact[:index]]
            atr_percent = strategy.calculate_atr_percent(history)
            if atr_percent is not None:
                atr_values[(date, code)] = atr_percent
    for date, pairs in candidates.items():
        for code, previous in pairs:
            atr_percent = atr_values.get((date, code))
            if atr_percent is not None and atr_percent >= strategy.MIN_ATR_PERCENT:
                filtered[date].append((code, previous))
    return filtered


def _collect_opportunities(xtdata, candidates, breadth_candidates,
                           trading_dates, index_history):
    opportunities = []
    date_position = {date: index for index, date in enumerate(trading_dates)}
    candidate_dates = [
        date for date in sorted(candidates)
        if date_position.get(date, len(trading_dates)) + 1 < len(trading_dates)]
    for sequence, entry_date in enumerate(candidate_dates, 1):
        exit_date = trading_dates[date_position[entry_date] + 1]
        pairs = candidates[entry_date]
        breadth_pairs = breadth_candidates.get(entry_date, [])
        codes = sorted(set(
            [code for code, _ in pairs] + [code for code, _ in breadth_pairs]))
        request_codes = codes + [bt11.strategy.INDEX_CODE]
        minute_data = bt10._read_minute_range(
            xtdata, request_codes, entry_date, exit_date)
        missing = [
            code for code in request_codes
            if not bt10._minute_data_complete(
                minute_data.get(code), entry_date, exit_date)]
        if missing:
            print('[MINUTE {}] downloading {}'.format(
                entry_date, len(missing)), flush=True)
            bt10._download_minute_with_timeout(missing, entry_date, exit_date)
            minute_data = bt10._read_minute_range(
                xtdata, request_codes, entry_date, exit_date)

        index_closes = index_history.get(entry_date, [])
        index_bars = bt10._minute_rows(
            minute_data.get(bt11.strategy.INDEX_CODE), entry_date)
        if len(index_closes) < 25 or not index_bars:
            print('[SIM {}/{}] {} skipped: index data'.format(
                sequence, len(candidate_dates), entry_date), flush=True)
            continue
        blocked = bt11._screen_times(
            entry_date, breadth_pairs, minute_data, index_bars, index_closes)
        completed = 0
        for code, previous in pairs:
            entry_bars = bt10._minute_rows(minute_data.get(code), entry_date)
            exit_bars = [
                row for row in bt10._minute_rows(minute_data.get(code), exit_date)
                if row[0] <= '10:00:00']
            trade = bt12.simulate_candidate_trade(
                code, entry_date, exit_date, previous, entry_bars, exit_bars,
                index_bars, index_closes, blocked)
            if trade:
                opportunities.append(trade)
                completed += 1
        print(
            '[SIM {}/{}] {} raw={} atr={} blocked_minutes={} trades={} total={}'.format(
                sequence, len(candidate_dates), entry_date, len(breadth_pairs),
                len(pairs), len(blocked), completed, len(opportunities)),
            flush=True)
    return opportunities


def _write_outputs(start, end, trades, metrics, opportunities, raw_candidates):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v13_atr7_{}_{}'.format(start, end)
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
        '# 中证500 MA5/MA20 隔夜策略 v13 ATR过滤回测', '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 唯一策略差异：在v12基础上要求买入前ATR14百分比不低于{:.1f}%'.format(
            strategy.MIN_ATR_PERCENT),
        '- ATR14百分比：最近14个完整交易日真实波幅均值 / 最近收盘价',
        '- 其他入场、退出、市场过滤、仓位和成本设置与v12相同', '',
        '## 结果', '',
        '- 原始候选股票日：{}'.format(raw_candidates),
        '- ATR过滤后独立机会数：{}'.format(opportunities),
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
        '- ATR阈值来自同一回测样本中的因子分析，存在过拟合风险。',
        '- 使用2026-08-31成分股快照，存在幸存者偏差。',
        '- 最大回撤基于已实现权益，不包含持仓期间浮动盈亏。',
    ]
    with open(report_path, 'w', encoding='utf-8') as file:
        file.write('\n'.join(lines) + '\n')
    return report_path, csv_path


def run_backtest(start='20250903', end='20260902'):
    xtdata = bt10._connect_xtdata()
    codes = bt10.load_bundled_constituents()
    daily_data = bt10._load_daily_data(xtdata, codes, start, end)
    raw_candidates, trading_dates, _ = bt10._build_candidates(
        daily_data, codes, start, end)
    raw_count = sum(len(value) for value in raw_candidates.values())
    candidates = filter_candidates_by_atr(daily_data, raw_candidates)
    atr_count = sum(len(value) for value in candidates.values())
    print('[ATR] candidate stock-days {}/{}'.format(atr_count, raw_count))
    index_history = bt11._index_history(xtdata, start, end)
    opportunities = _collect_opportunities(
        xtdata, candidates, raw_candidates, trading_dates, index_history)
    trades, final_cash, max_capital_used = bt11._allocate_portfolio(opportunities)
    bt10._add_names(xtdata, trades)
    metrics = bt10._metrics(trades, final_cash, max_capital_used)
    report_path, csv_path = _write_outputs(
        start, end, trades, metrics, len(opportunities), raw_count)
    print('[RESULT] trades={} win_rate={:.2f}% pnl={:,.2f} return={:.2f}%'.format(
        metrics['trades'], metrics['win_rate'], metrics['net_pnl'],
        metrics['return_pct']))
    print('[REPORT] {}'.format(report_path))
    print('[TRADES] {}'.format(csv_path))
    return 0


if __name__ == '__main__':
    raise SystemExit(run_backtest())
