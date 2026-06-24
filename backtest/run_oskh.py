# coding: utf-8
"""
OSkhQuant 原生引擎回测入口
============================
使用 OSkhQuant 真实的 KhTradeManager/KhConfig 进行回测，
数据由 baostock DataProvider 提供（替代 xtquant）。
策略通过 OkhAdapter 桥接为 OSkhQuant 信号格式。

结果输出到 backtest_results/<策略名_hash>/ (OSkhQuant 原生目录格式):
  trades.csv       — 交易记录
  daily_stats.csv  — 逐日净值和持仓
  summary.csv      — 总收益/年化收益/最大回撤/夏普比率
  benchmark.csv    — 基准指数数据
  config.csv       — 回测配置
"""
import os
import sys
import json
import hashlib
import argparse
import traceback
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd

# ── 路径设置 ──
BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKTEST_DIR)
OSKH_ROOT = r"c:\MyW\OSkhQuant"

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if OSKH_ROOT not in sys.path:
    sys.path.insert(0, OSKH_ROOT)

# ── 注入 mock xtquant 模块 (OSkhQuant 依赖 xtquant，用 mock 替代) ──
_MOCK_XT = os.path.join(BACKTEST_DIR, '_xtquant_mock')
if _MOCK_XT not in sys.path:
    sys.path.insert(0, _MOCK_XT)

from backtest.data_source import DataProvider
from backtest import config as bt_config


def _fmt_date(d):
    """datetime → YYYYMMDD"""
    if isinstance(d, datetime):
        return d.strftime('%Y%m%d')
    return str(d).replace('-', '')


def main():
    parser = argparse.ArgumentParser(description='OSkhQuant 原生引擎回测')
    parser.add_argument('--strategy', default=None, help='策略文件路径')
    parser.add_argument('--start', default='2020-01-01', help='开始日期')
    parser.add_argument('--end', default='2025-12-31', help='结束日期')
    args = parser.parse_args()

    strategy_path = args.strategy or bt_config.DEFAULT_STRATEGY
    if not os.path.isabs(strategy_path):
        strategy_path = os.path.join(PROJECT_ROOT, strategy_path)

    bt_start = args.start
    bt_end = args.end
    stock_pool = bt_config.STOCK_POOL
    benchmark_code = bt_config.BENCHMARK_CODE

    print("=" * 60)
    print("  OSkhQuant 原生引擎回测")
    print("=" * 60)
    print(f"  策略: {os.path.basename(strategy_path)}")
    print(f"  区间: {bt_start} ~ {bt_end}")
    print(f"  股票池: {len(stock_pool)} 只")
    print(f"  基准: {benchmark_code}")

    # ── 1. 加载数据 ──
    print("\n[1/5] 加载行情数据...")
    codes = list(set(stock_pool + [benchmark_code]))
    data = DataProvider()
    data.load(codes, bt_start, bt_end)
    data.validate()

    # ── 2. 创建 OSkhQuant 配置 ──
    print("[2/5] 创建 OSkhQuant 配置...")
    from khConfig import KhConfig

    # 创建临时 JSON 配置 (OSkhQuant KhConfig 需要文件)
    tmp_config_path = os.path.join(BACKTEST_DIR, 'output', '_oskh_config.json')
    os.makedirs(os.path.dirname(tmp_config_path), exist_ok=True)

    config_dict = {
        "system": {"run_mode": "backtest", "session_id": int(datetime.now().timestamp())},
        "account": {"account_id": "test_account", "account_type": "SECURITY_ACCOUNT"},
        "backtest": {
            "start_time": bt_start.replace('-', ''),
            "end_time": bt_end.replace('-', ''),
            "init_capital": bt_config.INITIAL_CAPITAL,
            "trade_cost": {
                "commission_rate": bt_config.COMMISSION_RATE,
                "min_commission": 5.0,
                "stamp_tax_rate": bt_config.STAMP_TAX_RATE,
                "transfer_fee_rate": 0.00001,
                "flow_fee": 0.1,
                "slippage": {"type": "ratio", "ratio": bt_config.SLIPPAGE},
            },
            "t0_mode": False,
        },
        "data": {
            "stock_list": stock_pool,
            "kline_period": "1d",
            "fields": ["open", "high", "low", "close", "volume", "amount"],
            "dividend_type": "none",
        },
        "risk": {"position_limit": 0.99, "order_limit": 200, "loss_limit": 0.50},
    }
    with open(tmp_config_path, 'w', encoding='utf-8') as f:
        json.dump(config_dict, f, ensure_ascii=False, indent=2)

    kh_config = KhConfig(tmp_config_path)

    # ── 3. 创建真实的 OSkhQuant 交易管理器 ──
    print("[3/5] 初始化 OSkhQuant KhTradeManager...")
    from khTrade import KhTradeManager

    trade_mgr = KhTradeManager(kh_config, callback=None)
    trade_mgr.init()

    # ── 初始化资产 (模拟 khFrame.init_data 的行为) ──
    trade_mgr.assets = {
        "account_type": 1,  # SECURITY_ACCOUNT
        "account_id": kh_config.account_id,
        "cash": float(bt_config.INITIAL_CAPITAL),
        "frozen_cash": 0.0,
        "market_value": 0.0,
        "total_asset": float(bt_config.INITIAL_CAPITAL),
    }
    trade_mgr.positions = {}
    trade_mgr.orders = {}
    trade_mgr.trades = {}
    trade_mgr._order_counter = 0
    trade_mgr._trade_counter = 0

    # ── 4. 构建日线回测循环 ──
    print("[4/5] 开始回测循环...")

    n_bars = len(data.dates_list)
    dates_list = data.dates_list

    # ── 结果存储 ──
    daily_stats = []
    all_trades = []

    # ── 加载策略 (通过适配器) ──
    from backtest.okh.adapter import QmtStrategyAdapter, OkhMockContext, _OkhMockAccount, _OkhMockPosition
    from backtest.okh.config import create_default_config as okh_create_config

    okh_cfg = okh_create_config(stock_pool, benchmark_code,
                                bt_start.replace('-', ''), bt_end.replace('-', ''),
                                bt_config.INITIAL_CAPITAL)
    adapter = QmtStrategyAdapter(strategy_path, data, okh_cfg)
    strategy_mod = adapter.load()

    # 连接适配器到我们的引擎状态
    class _BridgeEngine:
        def __init__(self):
            self._current_bar = 0
            self.trade_mgr = trade_mgr
            self.data = data

    bridge = _BridgeEngine()
    adapter._engine_ref = bridge

    # 调用策略 init
    ctx = OkhMockContext(data)
    ctx.stock_pool = stock_pool
    adapter._context = ctx
    strategy_mod['init'](stocks=stock_pool, data=None)

    # ── 日线循环 ──
    last_print_pct = 0
    market_bars = []  # for benchmark
    prev_date = None

    for bar in range(n_bars):
        # 进度
        pct = int((bar + 1) / n_bars * 100)
        if pct >= last_print_pct + 10:
            print(f"  [进度] {pct}% ({bar+1}/{n_bars}) | 持仓={len(trade_mgr.positions)} | 资产={trade_mgr.assets['total_asset']/10000:.0f}万")
            last_print_pct = pct

        current_date = dates_list[bar]
        bridge._current_bar = bar
        ctx.barpos = bar

        # ── 新交易日检测 ──
        is_new_day = prev_date is None or current_date.date() != prev_date.date()
        prev_date = current_date

        # ── T+1 解锁 (新交易日) ──
        if is_new_day:
            for pos in trade_mgr.positions.values():
                pos['can_use_volume'] = pos['volume']

        # ── 更新持仓市值 (遍历 OSkhQuant 原生持仓结构) ──
        total_mv = 0.0
        for code, pos in trade_mgr.positions.items():
            px = data.get_value(code, 'close', bar)
            if px and px > 0:
                px = float(px)
                pos['current_price'] = px
                pos['market_value'] = pos['volume'] * px
                total_mv += pos['market_value']
        trade_mgr.assets['market_value'] = total_mv
        trade_mgr.assets['total_asset'] = trade_mgr.assets['cash'] + total_mv

        # ── 调用策略 handlebar ──
        signals = strategy_mod['khHandlebar']({})

        # ── ★ 将信号喂给 OSkhQuant 真实的 KhTradeManager ──
        if signals:
            trade_mgr.process_signals(signals)

        # ── 日内循环结束：记录每日统计 ──
        benchmark_val = None
        bm_px = data.get_value(benchmark_code, 'close', bar)
        if bm_px and bm_px > 0:
            benchmark_val = float(bm_px)

        assets = trade_mgr.assets
        daily_stats.append({
            'date': current_date.strftime('%Y-%m-%d') if hasattr(current_date, 'strftime') else str(current_date),
            'total_asset': round(assets['total_asset'], 2),
            'cash': round(assets['cash'], 2),
            'market_value': round(assets['market_value'], 2),
            'positions_count': len(trade_mgr.positions),
            'benchmark': round(benchmark_val, 4) if benchmark_val else '',
        })

        # 记录基准
        if benchmark_val:
            market_bars.append({
                'date': current_date,
                'close': benchmark_val,
            })

    # ── 回测结束强制平仓 ──
    if trade_mgr.positions:
        final_signals = []
        for code, pos in list(trade_mgr.positions.items()):
            px = data.get_value(code, 'close', n_bars - 1)
            if px and px > 0:
                px = float(px)
                final_signals.append({
                    'code': code,
                    'action': 'sell',
                    'price': px,
                    'volume': pos['volume'],
                    'reason': '回测结束强制平仓',
                })
        if final_signals:
            trade_mgr.process_signals(final_signals)

    print(f"  [完成] {n_bars} 个交易日 | 终值={trade_mgr.assets['total_asset']/10000:.0f}万")

    # ── 5. 计算绩效并保存结果 ──
    print("[5/5] 计算绩效 & 保存结果...")

    # 策略文件 hash (用于输出目录，类似 OSkhQuant 行为)
    strategy_hash = hashlib.md5(open(strategy_path, 'rb').read()).hexdigest()[:8]
    output_dir = os.path.join(PROJECT_ROOT, 'backtest_results', strategy_hash)
    os.makedirs(output_dir, exist_ok=True)

    # ── 计算绩效 ──
    df = pd.DataFrame(daily_stats)
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    initial_value = df['total_asset'].iloc[0]
    final_value = df['total_asset'].iloc[-1]
    total_return = final_value / initial_value - 1
    n_days = len(df) - 1
    n_years = n_days / 252

    daily_ret = df['total_asset'].pct_change().dropna()
    ann_return = (final_value / initial_value) ** (1 / n_years) - 1 if n_years > 0 else 0
    volatility = float(daily_ret.std() * np.sqrt(252))
    rf = 0.03
    sharpe = (ann_return - rf) / volatility if volatility > 0 else 0

    # 最大回撤
    df['cumulative'] = (1 + df['total_asset'].pct_change()).fillna(1).cumprod()
    roll_max = df['cumulative'].cummax()
    df['drawdown'] = (df['cumulative'] - roll_max) / roll_max
    max_dd = float(df['drawdown'].min())
    dd_end_idx = df['drawdown'].idxmin()
    dd_start_idx = df['cumulative'][:dd_end_idx+1].idxmax() if dd_end_idx > 0 else 0
    dd_start = df['date'].iloc[dd_start_idx] if dd_start_idx < len(df) else None
    dd_end = df['date'].iloc[dd_end_idx] if dd_end_idx < len(df) else None

    # 基准对比
    bm_ret = 0.0
    alpha = 0.0
    beta = 0.0
    if 'benchmark' in df.columns and df['benchmark'].notna().any():
        bm_valid = df[df['benchmark'].notna() & (df['benchmark'] != '')]
        if len(bm_valid) > 10:
            bm_series = pd.to_numeric(bm_valid['benchmark'], errors='coerce')
            bm_rets = bm_series.pct_change().dropna()
            aligned = pd.DataFrame({'s': daily_ret.reindex(bm_rets.index), 'b': bm_rets}).dropna()
            if len(aligned) > 10:
                cov = aligned.cov().iloc[0, 1]
                var = aligned['b'].var()
                beta = cov / var if var > 0 else 0
                excess = aligned['s'] - beta * aligned['b']
                ann_excess = (1 + excess).prod() ** (252 / len(excess)) - 1
                alpha = ann_excess
            first_bm = float(bm_valid['benchmark'].iloc[0])
            last_bm = float(bm_valid['benchmark'].iloc[-1])
            if first_bm > 0:
                bm_ret = last_bm / first_bm - 1

    # ── P&L 计算 (买卖对匹配) ──
    # OSkhQuant trade: {order_type:23=buy/24=sell, stock_code, traded_volume, traded_price}
    trades = list(trade_mgr.trades.values())
    buy_trades = [t for t in trades if t.get('order_type') == 23]
    sell_trades = [t for t in trades if t.get('order_type') == 24]

    matched_pnls = []
    buy_queue = list(buy_trades)
    for sell in sell_trades:
        code = sell.get('stock_code', '')
        need_vol = sell.get('traded_volume', 0)
        matched_vol = 0
        matched_cost = 0.0
        while matched_vol < need_vol and buy_queue:
            found = False
            for i, b in enumerate(buy_queue):
                if b.get('stock_code') == code and b.get('traded_volume', 0) > 0:
                    use = min(need_vol - matched_vol, b['traded_volume'])
                    matched_cost += use * b.get('traded_price', 0)
                    matched_vol += use
                    b['traded_volume'] -= use
                    if b['traded_volume'] <= 0:
                        buy_queue.pop(i)
                    found = True
                    break
            if not found:
                break
        if matched_vol > 0:
            avg_cost = matched_cost / matched_vol
            sell_price = sell.get('traded_price', 0)
            cost_estimate = sell_price * matched_vol * 0.0012  # 约千1.2 手续费+印花税+滑点
            pnl = (sell_price - avg_cost) * matched_vol - cost_estimate
            matched_pnls.append(pnl)

    total_trades = len(matched_pnls)
    wins = [p for p in matched_pnls if p > 0]
    losses = [p for p in matched_pnls if p <= 0]
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else (float('inf') if wins else 0)

    # ── 保存 trades.csv ──
    trades_df = pd.DataFrame(trades)
    trades_file = os.path.join(output_dir, 'trades.csv')
    trades_df.to_csv(trades_file, index=False, encoding='utf-8-sig')
    print(f"  trades.csv — {len(trades_df)} 条")

    # ── 保存 daily_stats.csv ──
    daily_df = pd.DataFrame(daily_stats)
    daily_file = os.path.join(output_dir, 'daily_stats.csv')
    daily_df.to_csv(daily_file, index=False, encoding='utf-8-sig')
    print(f"  daily_stats.csv — {len(daily_df)} 行")

    # ── 保存 benchmark.csv ──
    if market_bars:
        bm_df = pd.DataFrame(market_bars)
        bm_df['date'] = bm_df['date'].apply(lambda d: d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d))
        bm_file = os.path.join(output_dir, 'benchmark.csv')
        bm_df.to_csv(bm_file, index=False, encoding='utf-8-sig')
        print(f"  benchmark.csv — {len(bm_df)} 行")

    # ── 保存 summary.csv ──
    summary = {
        'total_return': f"{total_return*100:.2f}%",
        'annual_return': f"{ann_return*100:.2f}%",
        'volatility': f"{volatility*100:.2f}%",
        'sharpe_ratio': round(sharpe, 3),
        'max_drawdown': f"{max_dd*100:.2f}%",
        'max_drawdown_start': str(dd_start),
        'max_drawdown_end': str(dd_end),
        'alpha': f"{alpha*100:.2f}%",
        'beta': round(beta, 3),
        'benchmark_return': f"{bm_ret*100:.2f}%",
        'total_trades': total_trades,
        'win_rate': f"{win_rate*100:.1f}%",
        'profit_factor': round(profit_factor, 2),
        'avg_win': f"{np.mean(wins):.0f}" if wins else '0',
        'avg_loss': f"{np.mean(losses):.0f}" if losses else '0',
        'final_value': final_value,
        'initial_value': initial_value,
    }
    summary_df = pd.DataFrame([summary])
    summary_file = os.path.join(output_dir, 'summary.csv')
    summary_df.to_csv(summary_file, index=False, encoding='utf-8-sig')
    print(f"  summary.csv — 1 行")

    # ── 保存 config.csv ──
    config_rows = [
        {'key': 'strategy', 'value': os.path.basename(strategy_path)},
        {'key': 'start_date', 'value': bt_start},
        {'key': 'end_date', 'value': bt_end},
        {'key': 'initial_capital', 'value': bt_config.INITIAL_CAPITAL},
        {'key': 'stock_pool_size', 'value': len(stock_pool)},
        {'key': 'benchmark', 'value': benchmark_code},
        {'key': 'commission_rate', 'value': bt_config.COMMISSION_RATE},
        {'key': 'stamp_tax_rate', 'value': bt_config.STAMP_TAX_RATE},
        {'key': 'slippage', 'value': bt_config.SLIPPAGE},
        {'key': 'engine', 'value': 'OSkhQuant KhTradeManager v2.1.4'},
    ]
    config_df = pd.DataFrame(config_rows)
    config_file = os.path.join(output_dir, 'config.csv')
    config_df.to_csv(config_file, index=False, encoding='utf-8-sig')
    print(f"  config.csv — {len(config_df)} 行")

    # ── 保存策略副本 ──
    import shutil
    strategy_copy = os.path.join(output_dir, os.path.basename(strategy_path))
    shutil.copy2(strategy_path, strategy_copy)

    # ── 打印报告 ──
    print()
    print("=" * 60)
    print("  OSkhQuant 回测绩效报告")
    print("=" * 60)
    print(f"  回测区间:  {bt_start} ~ {bt_end}")
    print(f"  交易天数:  {n_days}")
    print(f"  初始资金:  {initial_value:,.0f}")
    print(f"  最终权益:  {final_value:,.0f}")
    print()
    print(f"  [收益风险]")
    print(f"    总收益率:       {total_return*100:+.2f}%")
    print(f"    年化收益率:     {ann_return*100:+.2f}%")
    print(f"    年化波动率:     {volatility*100:.2f}%")
    print(f"    夏普比率:       {sharpe:.2f}")
    print(f"    最大回撤:       {max_dd*100:.2f}%")
    if dd_start is not None:
        print(f"    回撤区间:       {dd_start} ~ {dd_end}")
    print()
    print(f"  [基准对比] ({benchmark_code})")
    print(f"    Alpha:          {alpha*100:+.2f}%")
    print(f"    Beta:           {beta:.3f}")
    print(f"    基准收益:       {bm_ret*100:+.2f}%")
    print()
    print(f"  [交易统计]")
    print(f"    总交易次数:     {total_trades}")
    print(f"    胜率:           {win_rate*100:.1f}%")
    print(f"    盈亏比:         {profit_factor:.2f}")
    if wins:
        print(f"    平均盈利:       {np.mean(wins):,.0f}")
    if losses:
        print(f"    平均亏损:       {np.mean(losses):,.0f}")
    print()
    print(f"  结果目录: {output_dir}")
    print("=" * 60)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n回测异常: {e}")
        traceback.print_exc()
        sys.exit(1)
