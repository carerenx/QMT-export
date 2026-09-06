# -*- coding: utf-8 -*-
"""Ablation backtest for the v15 optimized selection rules."""

import csv
import math
import os
from collections import defaultdict

import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v10 as bt10
import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v11 as bt11
import Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_csi500_overnight_v12 as bt12
import Stragety.MiniQMT_Stragety.MA5MA20.CSI500_MA5_MA20_Overnight_v15_miniqmt as strategy


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')
VARIANTS = ('fixed', 'fixed_slope', 'atr_normalized', 'optimized')


def _candidate_upper(history, variant):
    closes = [row[2] for row in history]
    ma5 = sum(closes[-5:]) / 5.0
    if variant in ('fixed', 'fixed_slope', 'optimized'):
        return ma5 * (1.0 - strategy.FIXED_MA5_BELOW_PERCENT / 100.0)
    atr14 = strategy.calculate_atr(history)
    if atr14 is None:
        return None
    return ma5 - strategy.MIN_PULLBACK_ATR * atr14


def build_variant_candidates(daily_data, codes, start, end):
    candidates = {variant: defaultdict(list) for variant in VARIANTS}
    for code in codes:
        rows = bt10._daily_rows(daily_data.get(code))
        for index, row in enumerate(rows):
            date, _, high, low, _ = row
            if index < 25 or not start <= date <= end:
                continue
            history = [(item[2], item[3], item[4]) for item in rows[:index]]
            closes = [item[2] for item in history]
            ma20 = sum(closes[-20:]) / 20.0
            ma20_previous = sum(
                closes[-25:-strategy.MA20_SLOPE_LOOKBACK]) / 20.0
            for variant in VARIANTS:
                if variant != 'fixed' and ma20 <= ma20_previous:
                    continue
                if variant == 'optimized':
                    atr14 = strategy.calculate_atr(history)
                    if (atr14 is None or
                            atr14 / closes[-1] * 100.0 <
                            strategy.MIN_ATR_PERCENT):
                        continue
                upper = _candidate_upper(history, variant)
                if upper is not None and max(float(low), ma20) < min(
                        float(high), upper):
                    candidates[variant][date].append((code, history))
    return candidates


def simulate_candidate_trade(code, entry_date, exit_date, history, variant,
                             entry_bars, exit_bars, index_bars, index_closes,
                             blocked_screen_times):
    if not entry_bars:
        return None
    index_by_time = {row[0]: row[1:] for row in index_bars}
    day_open = float(entry_bars[0][1])
    watch = None
    screen_result = None
    buy_price = None
    buy_time = None
    previous_price = None

    for time_text, open_price, high, low, close in entry_bars:
        points = (open_price, high, low, close)
        if watch is None:
            index_price = bt11._market_price(index_by_time, time_text, 3)
            result = strategy.calculate_optimized_candidate(
                history, float(close), variant=variant)
            if (time_text not in blocked_screen_times and
                    index_price is not None and
                    strategy.v11.is_market_allowed(index_closes, index_price) and
                    result and result['matched']):
                watch = strategy.v11.new_buy_watch(
                    day_open, float(close), time_text)
                screen_result = result
                previous_price = float(close)
            continue

        if time_text in blocked_screen_times:
            previous_price = float(close)
            continue
        for point_index, raw_price in enumerate(points):
            price = float(raw_price)
            index_price = bt11._market_price(
                index_by_time, time_text, point_index)
            if (not math.isfinite(price) or price <= 0 or
                    index_price is None or
                    not strategy.v11.is_market_allowed(
                        index_closes, index_price)):
                previous_price = price
                continue
            if strategy.v11.advance_buy_watch(watch, price, time_text) == 'BUY':
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
        code, entry_date, exit_date, buy_time, buy_price, exit_bars)
    if trade and screen_result:
        trade.update({
            'screen_ma5': round(screen_result['ma5'], 6),
            'screen_ma20': round(screen_result['ma20'], 6),
            'screen_atr14': round(screen_result['atr14'], 6),
            'screen_atr_percent': round(screen_result['atr_percent'], 6),
            'screen_pullback_atr': round(screen_result['pullback_atr'], 6),
        })
    return trade


def collect_variant_opportunities(xtdata, candidates, breadth_candidates,
                                  trading_dates, index_history):
    opportunities = {variant: [] for variant in VARIANTS}
    date_position = {date: index for index, date in enumerate(trading_dates)}
    candidate_dates = sorted(set(
        date for variant in VARIANTS for date in candidates[variant]
        if date_position.get(date, len(trading_dates)) + 1 < len(trading_dates)))
    for sequence, entry_date in enumerate(candidate_dates, 1):
        exit_date = trading_dates[date_position[entry_date] + 1]
        breadth_pairs = breadth_candidates.get(entry_date, [])
        index_closes = index_history.get(entry_date, [])
        index_data = bt10._read_minute_range(
            xtdata, [strategy.v11.INDEX_CODE], entry_date, exit_date)
        index_bars = bt10._minute_rows(
            index_data.get(strategy.v11.INDEX_CODE), entry_date)
        market_has_entry_window = any(
            strategy.v11.is_market_allowed(index_closes, float(price))
            for row in index_bars for price in row[1:])
        if len(index_closes) < 25 or not market_has_entry_window:
            print('[SIM {}/{}] {} skipped: market filter'.format(
                sequence, len(candidate_dates), entry_date), flush=True)
            continue
        candidate_codes = [
            code for variant in VARIANTS
            for code, _ in candidates[variant].get(entry_date, [])]
        request_codes = sorted(set(
            candidate_codes + [code for code, _ in breadth_pairs]))
        request_codes.append(strategy.v11.INDEX_CODE)
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

        index_bars = bt10._minute_rows(
            minute_data.get(strategy.v11.INDEX_CODE), entry_date)
        if len(index_closes) < 25 or not index_bars:
            continue
        blocked = bt11._screen_times(
            entry_date, breadth_pairs, minute_data, index_bars, index_closes)
        counts = []
        for variant in VARIANTS:
            completed = 0
            for code, history in candidates[variant].get(entry_date, []):
                entry_bars = bt10._minute_rows(
                    minute_data.get(code), entry_date)
                exit_bars = [
                    row for row in bt10._minute_rows(
                        minute_data.get(code), exit_date)
                    if row[0] <= '10:00:00']
                trade = simulate_candidate_trade(
                    code, entry_date, exit_date, history, variant, entry_bars,
                    exit_bars, index_bars, index_closes, blocked)
                if trade:
                    opportunities[variant].append(trade)
                    completed += 1
            counts.append('{}={}'.format(variant, completed))
        print('[SIM {}/{}] {} {} total={}'.format(
            sequence, len(candidate_dates), entry_date, ' '.join(counts),
            '/'.join(str(len(opportunities[value])) for value in VARIANTS)),
            flush=True)
    return opportunities


def _write_outputs(start, end, final_trades, metrics_by_variant,
                   opportunities_by_variant, candidate_counts):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v15_optimized_{}_{}'.format(start, end)
    csv_path = os.path.join(OUTPUT_DIR, prefix + '_trades.csv')
    report_path = os.path.join(OUTPUT_DIR, prefix + '_report.md')
    fields = [
        'code', 'name', 'buy_date', 'buy_time', 'buy_price', 'sell_date',
        'sell_time', 'sell_price', 'shares', 'exit_reason', 'screen_ma5',
        'screen_ma20', 'screen_atr14', 'screen_pullback_atr',
        'screen_atr_percent',
        'buy_commission', 'sell_commission', 'stamp_tax', 'net_pnl',
        'return_pct']
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(final_trades)

    lines = [
        '# 中证500 MA5/MA20 隔夜策略 v15 选股优化回测', '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 最终规则：前5/20日固定均线、MA20五日斜率向上、低于MA5至少{:.1f}%、ATR14至少{:.1f}%'.format(
            strategy.FIXED_MA5_BELOW_PERCENT, strategy.MIN_ATR_PERCENT),
        '- 买卖机制、市场过滤、仓位、成本及无1.5%止损设置与v12相同', '',
        '## 消融结果', '',
        '| 版本 | 候选股票日 | 独立机会 | 成交 | 胜率 | 净利润 | PF | 最大回撤 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    labels = {
        'fixed': '固定MA，仍用3%回调',
        'fixed_slope': '固定MA + MA20向上',
        'atr_normalized': 'MA20向上 + 0.5ATR回调',
        'optimized': '完整v15（固定3% + ATR≥7%）',
    }
    for variant in VARIANTS:
        metrics = metrics_by_variant[variant]
        lines.append(
            '| {} | {} | {} | {} | {:.2f}% | {:,.2f}元 | {:.3f} | {:.2f}% |'.format(
                labels[variant], candidate_counts[variant],
                len(opportunities_by_variant[variant]), metrics['trades'],
                metrics['win_rate'], metrics['net_pnl'],
                metrics['profit_factor'],
                metrics['max_realized_drawdown_pct']))
    final_metrics = metrics_by_variant['optimized']
    reasons = ', '.join('{}={}'.format(key, value) for key, value in sorted(
        final_metrics['exit_reasons'].items()))
    lines.extend([
        '', '## 完整v15结果', '',
        '- 平均每笔：{:,.2f}元'.format(final_metrics['average_pnl']),
        '- 组合收益率：{:.2f}%'.format(final_metrics['return_pct']),
        '- 最大占用资金：{:,.2f}元'.format(final_metrics['max_capital_used']),
        '- 卖出原因：{}'.format(reasons or '-'), '',
        '## 限制', '',
        '- 参数来自同一年度样本中的分析，消融结果仍属于样本内证据。',
        '- 使用2026-08-31成分股快照，存在幸存者偏差。',
        '- 最大回撤基于已实现权益，不包含持仓期间浮动盈亏。',
    ])
    with open(report_path, 'w', encoding='utf-8') as file:
        file.write('\n'.join(lines) + '\n')
    return report_path, csv_path


def run_backtest(start='20250903', end='20260902'):
    xtdata = bt10._connect_xtdata()
    codes = bt10.load_bundled_constituents()
    daily_data = bt10._load_daily_data(xtdata, codes, start, end)
    breadth_candidates, trading_dates, _ = bt10._build_candidates(
        daily_data, codes, start, end)
    candidates = build_variant_candidates(daily_data, codes, start, end)
    candidate_counts = {
        variant: sum(len(value) for value in candidates[variant].values())
        for variant in VARIANTS}
    print('[CANDIDATES] {}'.format(' '.join(
        '{}={}'.format(key, candidate_counts[key]) for key in VARIANTS)))
    index_history = bt11._index_history(xtdata, start, end)
    opportunities = collect_variant_opportunities(
        xtdata, candidates, breadth_candidates, trading_dates, index_history)

    trades_by_variant = {}
    metrics_by_variant = {}
    for variant in VARIANTS:
        trades, final_cash, max_capital_used = bt11._allocate_portfolio(
            opportunities[variant])
        trades_by_variant[variant] = trades
        metrics_by_variant[variant] = bt10._metrics(
            trades, final_cash, max_capital_used)
        metrics = metrics_by_variant[variant]
        print('[RESULT {}] trades={} win={:.2f}% pnl={:,.2f} PF={:.3f}'.format(
            variant, metrics['trades'], metrics['win_rate'],
            metrics['net_pnl'], metrics['profit_factor']))
    bt10._add_names(xtdata, trades_by_variant['optimized'])
    report_path, csv_path = _write_outputs(
        start, end, trades_by_variant['optimized'], metrics_by_variant,
        opportunities, candidate_counts)
    print('[REPORT] {}'.format(report_path))
    print('[TRADES] {}'.format(csv_path))
    return 0


if __name__ == '__main__':
    raise SystemExit(run_backtest())
