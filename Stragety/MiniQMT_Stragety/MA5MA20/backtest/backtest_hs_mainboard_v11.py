# -*- coding: utf-8 -*-
"""HS main-board screening plus the prior v13 overnight execution model.

Run from repository root with: python -m
Stragety.MiniQMT_Stragety.MA5MA20.backtest.backtest_hs_mainboard_v11
Current universe/names only; this is not a point-in-time universe backtest.
"""
import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from Stragety.MiniQMT_Stragety.MA5MA20 import HS_MainBoard_MA5_MA20_Screener_v11_NoST_ATR_bigqmt_redis as screen
from Stragety.MiniQMT_Stragety.MA5MA20.backtest import backtest_csi500_overnight_v10 as bt10
from Stragety.MiniQMT_Stragety.MA5MA20.backtest import backtest_csi500_overnight_v11 as bt11
from Stragety.MiniQMT_Stragety.MA5MA20.backtest import backtest_csi500_overnight_v12 as bt12
from Stragety.MiniQMT_Stragety.MA5MA20.backtest import backtest_csi500_overnight_v13 as bt13

OUT = Path(__file__).resolve().parents[2] / 'backtest_output' / 'hs_mainboard_v11'


def _log(message):
    screen._log(message)


def read_bars(api, codes, period, start, end):
    data = {}
    for offset in range(0, len(codes), 50):
        batch = codes[offset:offset + 50]
        data.update(api.xtdata.get_market_data_ex(
            field_list=bt10.MINUTE_FIELDS, stock_list=batch, period=period,
            start_time=start, end_time=end, count=-1, dividend_type='front',
            fill_data=True, chunk_size=0, timeout_seconds=30) or {})
        if period == '1d':
            _log('[DAILY] {}/{}'.format(min(offset + 50, len(codes)), len(codes)))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='20250903')
    parser.add_argument('--end', default='20260902')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    api = screen.BigQmtRedisApi()
    api.verify()
    codes, source = api.universe()
    names_path = OUT / ('names_' + datetime.now().strftime('%Y%m%d') + '.json')
    names = json.loads(names_path.read_text(encoding='utf-8')) if names_path.exists() else {}
    pending = [code for code in codes if code not in names]

    def name_for(code):
        detail = api.xtdata.get_instrument_detail(code) or {}
        return code, str(detail.get('InstrumentName') or '').strip()

    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, (code, name) in enumerate(pool.map(name_for, pending), 1):
            names[code] = name
            if index % 100 == 0 or index == len(pending):
                names_path.write_text(json.dumps(names, ensure_ascii=False), encoding='utf-8')
                _log('[NAMES] {}/{}'.format(index, len(pending)))
    total_mainboard = len(codes)
    codes = [code for code in codes if screen.allowed_name(names.get(code))]
    _log('[UNIVERSE] mainboard={} nonST={}'.format(total_mainboard, len(codes)))
    daily = read_bars(api, codes + ['000905.SH'], '1d', '20250601', args.end)
    missing = [code for code in codes if len(bt10._daily_rows(daily.get(code))) < 20]
    raw, _, _ = bt10._build_candidates(daily, codes, args.start, args.end)
    candidates = bt13.filter_candidates_by_atr(daily, raw)
    index_rows = bt10._daily_rows(daily.get('000905.SH'))
    dates = [row[0] for row in index_rows if args.start <= row[0] <= args.end]
    if len(dates) < 200:
        raise RuntimeError('Insufficient index history: {} dates'.format(len(dates)))
    index_history = {row[0]: [v[4] for v in index_rows[:i]]
                     for i, row in enumerate(index_rows) if i >= 25}
    opportunities, incomplete = [], []
    _log('[CANDIDATES] raw={} ATR={}'.format(sum(map(len, raw.values())), sum(map(len, candidates.values()))))
    for pos, date in enumerate(dates[:-1]):
        pairs = candidates.get(date, [])
        if not pairs:
            continue
        exit_date = dates[pos + 1]
        requested = sorted({code for code, _ in raw[date]} | {'000905.SH'})
        minute = read_bars(api, requested, '1m', date + '000000', exit_date + '100000')
        absent = [code for code in requested if not bt10._minute_data_complete(minute.get(code), date, exit_date)]
        if absent:
            incomplete.append({'date': date, 'missing_codes': absent})
            coverage_path = OUT / 'incomplete_minutes.json'
            coverage_path.write_text(json.dumps(incomplete, ensure_ascii=False, indent=2), encoding='utf-8')
            raise RuntimeError(
                '{}: {} stocks lack complete minute history. No return report produced. '
                'Fill BigQMT history and retry; details: {}'.format(date, len(absent), coverage_path))
        index_bars = bt10._minute_rows(minute.get('000905.SH'), date)
        blocked = bt11._screen_times(date, raw[date], minute, index_bars, index_history[date])
        for code, previous in pairs:
            trade = bt12.simulate_candidate_trade(
                code, date, exit_date, previous, bt10._minute_rows(minute.get(code), date),
                [row for row in bt10._minute_rows(minute.get(code), exit_date) if row[0] <= '10:00:00'],
                index_bars, index_history[date], blocked)
            if trade:
                trade['name'] = names[code]
                opportunities.append(trade)
        _log('[SIM {}/{}] {} opportunities={}'.format(pos + 1, len(dates)-1, date, len(opportunities)))
    trades, cash, capital = bt11._allocate_portfolio(opportunities)
    metrics = bt10._metrics(trades, cash, capital)
    result = dict(start=args.start, end=args.end, mainboard=total_mainboard, non_st=len(codes),
                  missing_daily=missing, incomplete_minutes=incomplete, metrics=metrics,
                  opportunities=len(opportunities), universe_source=source)
    prefix = OUT / ('hs_mainboard_{}_{}'.format(args.start, args.end))
    prefix.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if trades:
        with prefix.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(trades[0]))
            writer.writeheader()
            writer.writerows(trades)
    report = [
        '# 沪深主板非ST MA5/MA20+ATR 隔夜回测', '',
        '区间：{} 至 {}；股票池：{}只。'.format(args.start, args.end, len(codes)),
        '执行沿用v13：低点稳定180秒反弹0.3%买入；次日冲高1.2%后回落0.1%卖出，否则10:00卖出，无1.5%止损。',
        '保留中证500指数趋势过滤和原广度上限20、最多8持仓/每日10笔、每笔100股、单股3%资金上限。',
        '成本沿用旧回测：佣金万2.5且最低5元，印花税千1，双边各5BP滑点；初始100万元。',
        '分钟OHLC假定路径，不能还原逐笔成交或涨跌停无法成交。', '',
        '```json', json.dumps(metrics, ensure_ascii=False, indent=2), '```', '',
        '数据不足20根日线：{}只；分钟不完整跳过：{}天。'.format(len(missing), len(incomplete)),
        '当前股票池和当前名称过滤ST，存在幸存者偏差及历史ST分类偏差。',
        'ATR阈值来自此前中证500样本；股票池扩大后的收益不可沿用旧结论。',
        '最大回撤为已实现权益回撤，不含浮亏。缺失日不能视为零收益；存在缺失时属于不完整回测。',
    ]
    prefix.with_suffix('.md').write_text('\n\n'.join(report), encoding='utf-8')
    _log('[RESULT] {}'.format(json.dumps(result['metrics'], ensure_ascii=False)))
    _log('[REPORT] {}'.format(prefix.with_suffix('.md')))


if __name__ == '__main__':
    main()
