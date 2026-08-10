# -*- coding: utf-8 -*-
"""
双均线趋势跟踪策略 — 本地回测脚本
===================================
直接在 VS Code 中运行, 无需 QMT 客户端.

用法:
    python output/run_dual_ma_backtest.py

依赖:
    pip install numpy pandas akshare matplotlib

原理:
    用项目的 backtest 模块模拟 QMT 运行时,
    MockContextInfo 替代真实的 ContextInfo,
    qmt_mock 替代 order_shares/get_trade_detail_data 等注入函数.
"""
import os
import sys
import logging
from datetime import datetime

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 非GUI后端, 避免弹窗阻塞
import matplotlib.pyplot as plt

# 中文字体
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

logging.basicConfig(level=logging.WARNING)  # 减少日志噪音
logger = logging.getLogger(__name__)

# ============================================================================
# 用户配置 — 在这里修改参数
# ============================================================================
STOCK_CODE = '601869'
STOCK_NAME = '长飞光纤'
STOCK_QMT = f'{STOCK_CODE}.SH'

BACKTEST_START = '2024-01-01'
BACKTEST_END = '2025-12-31'
INITIAL_CAPITAL = 1_000_000  # 初始资金 100万

# 策略参数 (与策略文件保持一致)
FAST_MA = 5
SLOW_MA = 20
TREND_MA = 60
VOLUME_RATIO = 1.2
MAX_POSITION_LOTS = 5
TRADE_LOT_SIZE = 100
COMMISSION_RATE = 0.00025   # 万2.5
STAMP_TAX_RATE = 0.001      # 千分之一(卖出)


# ============================================================================
# 简化的本地回测引擎
# ============================================================================

class SimpleBacktest:
    """轻量级回测引擎 — 不依赖 QMT mock, 独立运行"""

    def __init__(self, df, stock_code, stock_name):
        self.df = df
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.cash = INITIAL_CAPITAL
        self.position_shares = 0
        self.position_cost = 0.0
        self.trades = []
        self.equity = []
        self.daily_values = []

    def run(self):
        """逐日迭代运行策略"""
        closes = self.df['close'].values
        volumes = self.df['volume'].values
        dates = self.df.index

        # 计算均线
        fast_ma_arr = np.full(len(closes), np.nan)
        slow_ma_arr = np.full(len(closes), np.nan)
        trend_ma_arr = np.full(len(closes), np.nan)

        for i in range(len(closes)):
            fast_ma_arr[i] = np.mean(closes[max(0, i - FAST_MA + 1):i + 1])
            slow_ma_arr[i] = np.mean(closes[max(0, i - SLOW_MA + 1):i + 1])
            trend_ma_arr[i] = np.mean(closes[max(0, i - TREND_MA + 1):i + 1])

        state = 'EMPTY'  # EMPTY / HOLDING
        highest_price = 0.0

        for i in range(SLOW_MA + 2, len(closes)):
            date = dates[i]
            price = closes[i]
            volume = volumes[i]

            prev_fast = fast_ma_arr[i - 1]
            prev_slow = slow_ma_arr[i - 1]
            fast = fast_ma_arr[i]
            slow = slow_ma_arr[i]
            trend = trend_ma_arr[i]

            # 金叉/死叉检测
            golden_cross = (prev_fast <= prev_slow) and (fast > slow)
            dead_cross = (prev_fast >= prev_slow) and (fast < slow)

            # 成交量确认
            vol_start = max(0, i - 21)
            avg_vol = np.mean(volumes[vol_start:i]) if i > 0 else volume
            volume_ok = volume > avg_vol * VOLUME_RATIO

            # 趋势过滤
            trend_ok = price > trend

            # ── 更新持仓最高价 ──
            if state == 'HOLDING' and price > highest_price:
                highest_price = price

            # ── 风控检查 ──
            if state == 'HOLDING' and self.position_shares > 0:
                cost = self.position_cost
                # 止损
                if price <= cost * 0.95:
                    self._sell(price, date, '止损')
                    state = 'EMPTY'
                    self.position_shares = 0
                    continue
                # 止盈
                if price >= cost * 1.15:
                    self._sell(price, date, '止盈')
                    state = 'EMPTY'
                    self.position_shares = 0
                    continue
                # 回撤止盈
                if highest_price > cost * 1.075:
                    drawdown = (highest_price - price) / highest_price
                    if drawdown >= 0.08:
                        self._sell(price, date, '回撤止盈')
                        state = 'EMPTY'
                        self.position_shares = 0
                        continue

            # ── 金叉买入 ──
            if golden_cross and volume_ok and trend_ok and state == 'EMPTY':
                max_shares = MAX_POSITION_LOTS * TRADE_LOT_SIZE
                need_cash = price * TRADE_LOT_SIZE * 1.001
                lots = min(int(self.cash // need_cash), max_shares // TRADE_LOT_SIZE)
                if lots > 0:
                    shares = lots * TRADE_LOT_SIZE
                    self._buy(price, shares, date)
                    state = 'HOLDING'
                    self.position_cost = price
                    highest_price = price
                    self.position_shares = shares

            # ── 死叉卖出 ──
            if dead_cross and state == 'HOLDING' and self.position_shares > 0:
                self._sell(price, date, '死叉卖出')
                state = 'EMPTY'
                self.position_shares = 0

            # 每日记录
            pos_value = self.position_shares * price
            total = self.cash + pos_value
            self.daily_values.append({
                'date': date, 'price': price, 'cash': self.cash,
                'position_value': pos_value, 'total': total,
                'state': state, 'fast_ma': fast, 'slow_ma': slow,
            })

        return self.daily_values

    def _buy(self, price, shares, date):
        amount = price * shares
        commission = amount * COMMISSION_RATE
        self.cash -= (amount + commission)
        self.trades.append({
            'date': date, 'action': 'BUY', 'price': price,
            'shares': shares, 'amount': amount, 'commission': commission,
        })

    def _sell(self, price, date, reason):
        shares = self.position_shares
        amount = price * shares
        commission = amount * COMMISSION_RATE
        stamp_tax = amount * STAMP_TAX_RATE
        self.cash += (amount - commission - stamp_tax)
        pnl = (price - self.position_cost) * shares - commission - stamp_tax
        self.trades.append({
            'date': date, 'action': 'SELL', 'price': price,
            'shares': shares, 'amount': amount, 'commission': commission,
            'stamp_tax': stamp_tax, 'pnl': pnl, 'reason': reason,
        })

    def report(self):
        """输出回测报告"""
        if not self.daily_values:
            print("[!] 无回测数据")
            return

        dv = pd.DataFrame(self.daily_values)
        dv['date'] = pd.to_datetime(dv['date'])
        dv.set_index('date', inplace=True)

        sell_trades = [t for t in self.trades if t['action'] == 'SELL']
        win_trades = [t for t in sell_trades if t['pnl'] > 0]

        total_return = (dv['total'].iloc[-1] / INITIAL_CAPITAL - 1) * 100
        daily_returns = dv['total'].pct_change().dropna()
        sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                  if daily_returns.std() > 0 else 0)
        peak = dv['total'].cummax()
        drawdown = ((dv['total'] - peak) / peak).min() * 100 if len(peak) > 0 else 0

        print(f"\n{'='*60}")
        print(f"  {STOCK_NAME}({STOCK_CODE}) 双均线趋势跟踪 — 回测报告")
        print(f"  区间: {BACKTEST_START} ~ {BACKTEST_END}")
        print(f"{'='*60}")
        print(f"  初始资金:     Y{INITIAL_CAPITAL:,.0f}")
        print(f"  最终资产:     Y{dv['total'].iloc[-1]:,.0f}")
        print(f"  总收益率:     {total_return:+.2f}%")
        print(f"  年化收益:     {(dv['total'].iloc[-1]/INITIAL_CAPITAL)**(252/len(dv))-1:+.2%}")
        print(f"  夏普比率:     {sharpe:.2f}")
        print(f"  最大回撤:     {drawdown:.2f}%")
        print(f"  交易次数:     {len(self.trades)} (买{len([t for t in self.trades if t['action']=='BUY'])}/卖{len(sell_trades)})")
        print(f"  胜率:         {len(win_trades)}/{len(sell_trades)} = {len(win_trades)/max(1,len(sell_trades)):.1%}")
        if sell_trades:
            total_pnl = sum(t['pnl'] for t in sell_trades)
            avg_win = np.mean([t['pnl'] for t in win_trades]) if win_trades else 0
            avg_loss = np.mean([t['pnl'] for t in sell_trades if t['pnl'] <= 0]) if len(sell_trades) > len(win_trades) else 0
            print(f"  总盈亏:       Y{total_pnl:,.0f}")
            print(f"  平均盈利:     Y{avg_win:,.0f}")
            print(f"  平均亏损:     Y{avg_loss:,.0f}")
            if avg_loss != 0 and avg_win != 0:
                print(f"  盈亏比:       {abs(avg_win/avg_loss):.2f}")
        print(f"{'='*60}")
        print(f"\n  交易明细:")
        for t in self.trades:
            if t['action'] == 'BUY':
                print(f"    {t['date'].strftime('%Y-%m-%d')}  买入 Y{t['price']:.2f} × {t['shares']}股 = Y{t['amount']:,.0f}")
            else:
                print(f"    {t['date'].strftime('%Y-%m-%d')}  卖出 Y{t['price']:.2f} × {t['shares']}股 "
                      f"| Y{t['pnl']:+,.0f} | {t['reason']}")

        return dv


# ============================================================================
# 主程序
# ============================================================================

def main():
    print(f"[数据] 正在获取 {STOCK_CODE} 历史数据...")

    # 用 baostock 获取数据 (与项目 backtest 模块一致, 最稳定)
    try:
        import baostock as bs

        lg = bs.login()
        if lg.error_code != '0':
            print(f"[错误] baostock 登录失败: {lg.error_msg}")
            return

        fields = 'date,open,high,low,close,preclose,volume,amount'
        bs_code = f'{"sh" if STOCK_CODE.startswith("6") else "sz"}.{STOCK_CODE}'
        rs = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=BACKTEST_START,
            end_date=BACKTEST_END,
            frequency='d', adjustflag='3'  # 前复权
        )
        if rs.error_code != '0':
            print(f"[错误] 查询失败: {rs.error_msg}")
            bs.logout()
            return

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        bs.logout()

        if not rows:
            print(f"[错误] 无数据: {bs_code}")
            return

        df = pd.DataFrame(rows, columns=fields.split(','))
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df.sort_index()
        print(f"[数据] baostock: {len(df)} 条日线数据")

    except ImportError:
        print("[错误] 请安装 baostock: pip install baostock")
        return
    except Exception as e:
        print(f"[错误] 数据获取失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 过滤日期范围
    df = df[(df.index >= BACKTEST_START) & (df.index <= BACKTEST_END)]
    if df.empty:
        print(f"[错误] 日期范围内无数据: {BACKTEST_START} ~ {BACKTEST_END}")
        return

    print(f"[回测] 区间 {BACKTEST_START} ~ {BACKTEST_END}, {len(df)} 个交易日")
    print(f"[回测] 初始资金 Y{INITIAL_CAPITAL:,.0f}, 参数: MA{FAST_MA}/MA{SLOW_MA}/MA{TREND_MA}")

    # 运行回测
    bt = SimpleBacktest(df, STOCK_CODE, STOCK_NAME)
    daily_values = bt.run()
    dv = bt.report()

    # ── 绘图 ──
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                             gridspec_kw={'height_ratios': [3, 1, 1]})

    # 图1: K线 + 均线 + 买卖点
    ax1 = axes[0]
    ax1.plot(dv.index, dv['price'], color='#333333', linewidth=0.8, alpha=0.5, label='收盘价')
    ax1.plot(dv.index, dv['fast_ma'], color='#2196F3', linewidth=1.2, label=f'MA{FAST_MA}快线')
    ax1.plot(dv.index, dv['slow_ma'], color='#FF9800', linewidth=1.2, label=f'MA{SLOW_MA}慢线')

    # 标记买卖点
    for t in bt.trades:
        if t['action'] == 'BUY':
            ax1.scatter(t['date'], t['price'], marker='^', color='red', s=100, zorder=5)
        else:
            ax1.scatter(t['date'], t['price'], marker='v', color='green', s=100, zorder=5)

    ax1.set_ylabel('价格 (Y)')
    ax1.set_title(f'{STOCK_NAME}({STOCK_CODE}) 双均线趋势跟踪 — MA{FAST_MA}/MA{SLOW_MA}/MA{TREND_MA}')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)

    # 图2: 资金曲线
    ax2 = axes[1]
    ax2.plot(dv.index, dv['total'], color='#4CAF50', linewidth=1.5, label='总资产')
    ax2.axhline(y=INITIAL_CAPITAL, color='gray', linestyle='--', alpha=0.5, label='初始资金')
    ax2.fill_between(dv.index, INITIAL_CAPITAL, dv['total'],
                     where=dv['total'] >= INITIAL_CAPITAL, color='green', alpha=0.15)
    ax2.fill_between(dv.index, INITIAL_CAPITAL, dv['total'],
                     where=dv['total'] < INITIAL_CAPITAL, color='red', alpha=0.15)
    ax2.set_ylabel('总资产 (Y)')
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)

    # 图3: 成交量
    ax3 = axes[2]
    colors_vol = ['red' if dv['price'].iloc[i] >= dv['price'].iloc[i-1] else 'green'
                  for i in range(1, len(dv))]
    # 对齐长度
    ax3.bar(dv.index[1:], df['volume'].values[-len(dv)+1:] if len(df) >= len(dv) else df['volume'].values[1:],
            color=colors_vol if len(colors_vol) == len(dv)-1 else 'gray',
            alpha=0.6, width=0.8)
    ax3.set_ylabel('成交量')
    ax3.set_xlabel('日期')
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()

    # 保存图片
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f'dual_ma_backtest_{STOCK_CODE}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
    )
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"\n[图表] 已保存至: {output_path}")


if __name__ == '__main__':
    main()
