# -*- coding: utf-8 -*-
"""V11 backtest with the next-day 1.5 percent stop-loss removed."""

import csv
import math
import os

import backtest_csi500_overnight_v10 as bt10
import backtest_csi500_overnight_v11 as bt11
import CSI500_MA5_MA20_Overnight_v12_miniqmt as strategy


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_output')
_V11_SIMULATOR = bt11.simulate_candidate_trade


def _exit_without_stop(code, entry_date, exit_date, buy_time, buy_price,
                       exit_bars):
    state = strategy.new_sell_watch(entry_date, buy_price)
    previous_price = None
    for time_text, open_price, high, low, close in exit_bars:
        if time_text >= strategy.v11.FORCE_SELL_TIME:
            return bt11._trade(
                code, entry_date, exit_date, buy_time, buy_price,
                time_text, bt11._slipped(open_price, 'SELL'), 'FORCE_1000')
        for raw_price in (open_price, high, low, close):
            price = float(raw_price)
            if not math.isfinite(price) or price <= 0:
                continue
            old_phase = state['phase']
            event = strategy.advance_sell_watch(
                state, exit_date, time_text, price)
            if event == 'SELLBACK':
                peak = float(state['sell_peak_price'])
                threshold = peak * (1.0 - strategy.v11.SELL_PULLBACK_PCT)
                raw_fill = bt11._continuous_fill(
                    previous_price, price, threshold, 'DOWN')
                return bt11._trade(
                    code, entry_date, exit_date, buy_time, buy_price,
                    time_text, bt11._slipped(raw_fill, 'SELL'), 'SELLBACK')
            if old_phase == 'BOUGHT' and state['phase'] == 'SPIKING':
                previous_price = price
                continue
            previous_price = price
    return None


def simulate_candidate_trade(code, entry_date, exit_date, previous_closes,
                             entry_bars, exit_bars, index_bars, index_closes,
                             blocked_screen_times):
    trade = _V11_SIMULATOR(
        code, entry_date, exit_date, previous_closes, entry_bars, exit_bars,
        index_bars, index_closes, blocked_screen_times)
    if not trade or trade['exit_reason'] != 'STOP_LOSS':
        return trade
    return _exit_without_stop(
        code, entry_date, exit_date, trade['buy_time'], trade['buy_price'],
        exit_bars)


def _write_outputs(start, end, trades, metrics, opportunities):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    prefix = 'csi500_overnight_v12_no_stop_{}_{}'.format(start, end)
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
        '# 中证500 MA5/MA20 隔夜策略 v12 无止损回测', '',
        '- 回测区间：{} 至 {}'.format(start, end),
        '- 唯一策略差异：删除v11次日1.5%止损',
        '- 卖出：上涨1.2%后回落0.1%，否则10:00强制卖出',
        '- 其他入场、市场过滤、仓位和成本设置与v11相同', '',
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
    original = bt11.simulate_candidate_trade
    bt11.simulate_candidate_trade = simulate_candidate_trade
    try:
        opportunities = bt11._collect_opportunities(
            xtdata, candidates, trading_dates, index_history)
    finally:
        bt11.simulate_candidate_trade = original
    trades, final_cash, max_capital_used = bt11._allocate_portfolio(opportunities)
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
