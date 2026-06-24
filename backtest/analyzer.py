"""
绩效分析与可视化

计算回测绩效指标，生成净值曲线图，保存交易记录。
"""
import os
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from . import config

logger = logging.getLogger(__name__)


def _setup_chinese_font():
    """设置 matplotlib 中文字体"""
    import matplotlib
    import matplotlib.font_manager as fm

    # 尝试常见中文字体
    zh_fonts = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'Arial Unicode MS']
    for font in zh_fonts:
        try:
            fm.findfont(font, fallback_to_default=False)
            matplotlib.rcParams['font.sans-serif'] = [font]
            matplotlib.rcParams['axes.unicode_minus'] = False
            return True
        except Exception:
            continue
    # 回退
    matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
    return False


class PerformanceAnalyzer:
    """
    绩效分析器

    接收引擎的 equity_curve 和 trades，计算指标并输出报告。
    """

    def __init__(self, engine):
        self.equity_curve = engine.equity_curve
        self.trades = engine.trades
        self.cash = engine.cash
        self._total_buy_amount = engine._total_buy_amount
        self._total_sell_amount = engine._total_sell_amount
        self._total_commission = engine._total_commission
        self._total_stamp_tax = engine._total_stamp_tax
        self.metrics = {}
        self.df = None
        self.drawdown_series = None

    def calculate(self):
        """计算所有绩效指标"""
        if not self.equity_curve:
            print("[分析] 无数据 — 回测尚未运行")
            return self.metrics

        df = pd.DataFrame(self.equity_curve)
        df['return'] = df['total_value'].pct_change()
        df['cumulative'] = (1 + df['return']).fillna(1).cumprod()
        self.df = df

        total_days = len(df) - 1
        if total_days < 1:
            return self.metrics

        # ---- 收益率 ----
        total_return = df['cumulative'].iloc[-1] - 1
        initial_value = config.INITIAL_CAPITAL
        final_value = df['total_value'].iloc[-1]

        # ---- 年化收益率 ----
        n_years = total_days / 252
        ann_return = (final_value / initial_value) ** (1 / n_years) - 1 if n_years > 0 else 0

        # ---- 年化波动率 ----
        daily_returns = df['return'].dropna()
        volatility = float(daily_returns.std() * np.sqrt(252))

        # ---- 夏普比率 (无风险利率 3%) ----
        rf = 0.03
        sharpe = (ann_return - rf) / volatility if volatility > 0 else 0

        # ---- 最大回撤 ----
        roll_max = df['cumulative'].cummax()
        drawdown = (df['cumulative'] - roll_max) / roll_max
        max_dd = float(drawdown.min())
        self.drawdown_series = drawdown

        # 最大回撤持续时间
        dd_start, dd_end = self._calc_drawdown_duration(drawdown)

        # ---- 交易统计 ----
        sell_trades = [t for t in self.trades if t['direction'] in ('sell', 'liquidate')]
        # 按股票合并为完整交易
        closed_trades = self._merge_trades()

        total_trades = len(closed_trades)
        wins = [t for t in closed_trades if t['pnl'] > 0]
        losses = [t for t in closed_trades if t['pnl'] <= 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0

        # ---- 盈亏比 ----
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0
        profit_factor = (
            abs(sum(t['pnl'] for t in wins) / sum(t['pnl'] for t in losses))
            if losses and sum(t['pnl'] for t in losses) != 0
            else float('inf')
        )

        # 最大连续亏损
        max_consec_losses = 0
        cur_consec = 0
        for t in closed_trades:
            if t['pnl'] <= 0:
                cur_consec += 1
                max_consec_losses = max(max_consec_losses, cur_consec)
            else:
                cur_consec = 0

        # 最大单笔盈利/亏损
        all_pnls = [t['pnl'] for t in closed_trades]
        max_win = max(all_pnls) if all_pnls else 0
        max_loss = min(all_pnls) if all_pnls else 0

        # 平均持仓天数
        avg_hold_days = np.mean([t.get('hold_days', 0) for t in closed_trades]) if closed_trades else 0

        self.metrics = {
            'start_date': df['date'].iloc[0],
            'end_date': df['date'].iloc[-1],
            'total_days': total_days,
            'initial_capital': initial_value,
            'final_value': final_value,
            'total_return': total_return,
            'annual_return': ann_return,
            'volatility': volatility,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'max_drawdown_pct': max_dd * 100,
            'max_drawdown_start': dd_start,
            'max_drawdown_end': dd_end,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_win': max_win,
            'max_loss': max_loss,
            'max_consecutive_losses': max_consec_losses,
            'avg_hold_days': avg_hold_days,
            'total_commission': self._total_commission,
            'total_stamp_tax': self._total_stamp_tax,
            'total_buy_amount': self._total_buy_amount,
            'total_sell_amount': self._total_sell_amount,
        }

        # ---- 月度/年度收益 ----
        self._calc_period_returns(df)

        # ---- 基准相对指标 (Alpha/Beta/IR) ----
        self._calc_benchmark_metrics(df)

        return self.metrics

    def _calc_drawdown_duration(self, drawdown):
        """计算最大回撤的起止时间"""
        if self.df is None or drawdown.empty:
            return None, None
        dd_end_idx = drawdown.idxmin()
        if pd.isna(dd_end_idx):
            return None, None
        # 找到这个最低点之前最高点的位置
        cum = self.df['cumulative']
        peak_before = cum[:dd_end_idx + 1].idxmax() if dd_end_idx > 0 else 0
        dd_start = self.df['date'].iloc[peak_before] if not isinstance(peak_before, int) else self.df['date'].iloc[0]
        dd_end_val = self.df['date'].iloc[dd_end_idx]
        return dd_start, dd_end_val

    def _merge_trades(self):
        """
        将 buy/sell 交易合并为完整交易记录。

        对于每个股票，找到第一笔买入和对应的卖出，计算盈亏。
        """
        # 按股票分组
        by_code = {}
        for t in self.trades:
            code = t['code']
            if code not in by_code:
                by_code[code] = []
            by_code[code].append(t)

        merged = []
        for code, trades in by_code.items():
            # 按日期排序
            trades.sort(key=lambda x: x['date'])
            buy_positions = []  # 未平仓的买入
            for t in trades:
                if t['direction'] == 'buy':
                    buy_positions.append({
                        'shares': t['shares'],
                        'price': t['price'],
                        'date': t['date'],
                    })
                elif t['direction'] in ('sell', 'liquidate'):
                    sell_shares = t['shares']
                    total_cost = 0
                    total_buy_shares = 0
                    buy_date = None
                    while sell_shares > 0 and buy_positions:
                        bp = buy_positions[0]
                        used = min(sell_shares, bp['shares'])
                        total_cost += used * bp['price']
                        total_buy_shares += used
                        buy_date = bp['date']
                        bp['shares'] -= used
                        sell_shares -= used
                        if bp['shares'] <= 0:
                            buy_positions.pop(0)

                    if total_buy_shares > 0:
                        avg_cost = total_cost / total_buy_shares
                        pnl = (t['price'] - avg_cost) * t['shares'] - t['commission'] - t['stamp_tax']
                        hold_days = (t['date'] - buy_date).days if buy_date else 0
                        merged.append({
                            'code': code,
                            'buy_date': buy_date,
                            'sell_date': t['date'],
                            'shares': t['shares'],
                            'buy_price': round(avg_cost, 3),
                            'sell_price': round(t['price'], 3),
                            'pnl': round(pnl, 2),
                            'hold_days': hold_days,
                        })
        return merged

    def _calc_period_returns(self, df):
        """计算月度 / 年度收益率"""
        df_monthly = df.set_index('date')['total_value'].resample('ME').last()
        self.monthly_returns = df_monthly.pct_change().dropna()
        self.monthly_table = self.monthly_returns.groupby(
            [self.monthly_returns.index.year, self.monthly_returns.index.month]
        ).apply(lambda x: (1 + x).prod() - 1)

        df_yearly = df.set_index('date')['total_value'].resample('YE').last()
        self.yearly_returns = df_yearly.pct_change().dropna()

    def _calc_benchmark_metrics(self, df):
        """计算 Alpha, Beta, Information Ratio (需基准数据)"""
        benchmark_code = getattr(config, 'BENCHMARK_CODE', '000905.SH')
        # 尝试从 engine 获取基准数据
        engine = getattr(self, '_engine', None)
        if engine is None or not hasattr(engine, 'data'):
            self.metrics['alpha'] = 0.0
            self.metrics['beta'] = 0.0
            self.metrics['information_ratio'] = 0.0
            return

        try:
            data_provider = engine.data
            bm_prices = []
            for date in df['date']:
                # Find bar index for this date
                bar = None
                for i, d in enumerate(data_provider.dates_list):
                    if d == date:
                        bar = i
                        break
                if bar is not None:
                    px = data_provider.get_value(benchmark_code, 'close', bar)
                    bm_prices.append(float(px) if px else None)
                else:
                    bm_prices.append(None)

            bm_series = pd.Series(bm_prices, index=df.index)
            bm_returns = bm_series.pct_change()

            # Align strategy and benchmark returns
            strategy_ret = df['return'].dropna()
            aligned = pd.DataFrame({'strategy': strategy_ret, 'benchmark': bm_returns}).dropna()

            if len(aligned) > 10:
                # Beta
                cov = aligned.cov().iloc[0, 1]
                var = aligned['benchmark'].var()
                beta = cov / var if var > 0 else 0

                # Alpha (annualized excess return)
                excess = aligned['strategy'] - beta * aligned['benchmark']
                ann_excess = (1 + excess).prod() ** (252 / len(excess)) - 1

                # Information Ratio
                tracking_err = excess.std() * np.sqrt(252)
                info_ratio = ann_excess / tracking_err if tracking_err > 0 else 0

                self.metrics['alpha'] = ann_excess
                self.metrics['beta'] = beta
                self.metrics['information_ratio'] = info_ratio
            else:
                self.metrics['alpha'] = 0.0
                self.metrics['beta'] = 0.0
                self.metrics['information_ratio'] = 0.0
        except Exception:
            self.metrics['alpha'] = 0.0
            self.metrics['beta'] = 0.0
            self.metrics['information_ratio'] = 0.0

    def print_report(self):
        """打印绩效报告到控制台"""
        if not self.metrics:
            print("[报告] 请先调用 calculate()")
            return

        m = self.metrics
        sep = "=" * 56

        print()
        print(sep)
        print("  回测绩效报告")
        print(sep)
        print("  [基础信息]")
        print("    回测区间:  %s  ~  %s" % (
            m['start_date'].strftime('%Y-%m-%d') if hasattr(m['start_date'], 'strftime') else m['start_date'],
            m['end_date'].strftime('%Y-%m-%d') if hasattr(m['end_date'], 'strftime') else m['end_date']))
        print("    交易天数:  %d" % m['total_days'])
        print("    初始资金:  %.2f" % m['initial_capital'])
        print("    最终权益:  %.2f" % m['final_value'])
        print()

        print("  [收益风险]")
        print("    总收益率:       %+.2f%%" % (m['total_return'] * 100))
        print("    年化收益率:     %+.2f%%" % (m['annual_return'] * 100))
        print("    年化波动率:     %.2f%%" % (m['volatility'] * 100))
        print("    夏普比率:       %.2f" % m['sharpe_ratio'])
        print("    最大回撤:       %.2f%%" % (m['max_drawdown'] * 100))
        if m.get('alpha', 0) != 0 or m.get('beta', 0) != 0:
            print("    Alpha:          %+.2f%%" % (m.get('alpha', 0) * 100))
            print("    Beta:           %.3f" % m.get('beta', 0))
            print("    信息比率:       %.2f" % m.get('information_ratio', 0))
        if m.get('max_drawdown_start'):
            dd_start = m['max_drawdown_start'].strftime('%Y-%m-%d') if hasattr(m['max_drawdown_start'], 'strftime') else str(m['max_drawdown_start'])
            dd_end = m['max_drawdown_end'].strftime('%Y-%m-%d') if hasattr(m['max_drawdown_end'], 'strftime') else str(m['max_drawdown_end'])
            print("    最大回撤区间:  %s ~ %s" % (dd_start, dd_end))
        print()

        print("  [交易统计]")
        print("    总交易次数:     %d" % m['total_trades'])
        print("    胜率:           %.1f%%" % (m['win_rate'] * 100))
        print("    盈亏比:         %.2f" % m['profit_factor'])
        print("    平均盈利:       %.2f" % m['avg_win'])
        print("    平均亏损:       %.2f" % m['avg_loss'])
        print("    最大盈利:       %.2f" % m['max_win'])
        print("    最大亏损:       %.2f" % m['max_loss'])
        print("    最大连亏:       %d 次" % m['max_consecutive_losses'])
        print("    平均持仓天数:   %.1f" % m['avg_hold_days'])
        print()

        print("  [费用]")
        print("    总佣金:         %.2f" % m['total_commission'])
        print("    总印花税:       %.2f" % m['total_stamp_tax'])
        print("    总成交金额:     %.2f (买 %.2f + 卖 %.2f)" % (
            m['total_buy_amount'] + m['total_sell_amount'],
            m['total_buy_amount'], m['total_sell_amount']))
        print()

        # 年度收益
        if hasattr(self, 'yearly_returns') and len(self.yearly_returns) > 0:
            print("  [年度收益]")
            for year, ret in self.yearly_returns.items():
                year_val = year if isinstance(year, int) else year.year
                print("    %d 年: %+.2f%%" % (year_val, ret * 100))
        print()

        target_sharpe = 1.2
        target_dd = -15
        print(sep)
        print("  目标: 夏普 > %.1f (当前 %.2f), 最大回撤 > %.0f%% (当前 %.2f%%)" % (
            target_sharpe, m['sharpe_ratio'], target_dd, m['max_drawdown'] * 100))
        status = "达标" if (m['sharpe_ratio'] > target_sharpe and m['max_drawdown'] > target_dd / 100) else "未达标"
        print("  综合评估: %s" % status)
        print(sep)
        print()

    def plot_equity_curve(self, save_path=None):
        """绘制净值曲线 + 回撤图"""
        if self.df is None:
            print("[绘图] 无数据")
            return

        try:
            import matplotlib
            matplotlib.use('Agg')  # 非交互模式
            import matplotlib.pyplot as plt
            _setup_chinese_font()
        except ImportError:
            print("[绘图] matplotlib 未安装, 跳过")
            return

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [3, 1]})

        # ---- 上: 净值曲线 ----
        dates = self.df['date']
        ax1.plot(dates, self.df['total_value'], label='策略净值', linewidth=1.5, color='#1f77b4')
        ax1.axhline(y=config.INITIAL_CAPITAL, color='gray', linestyle='--', alpha=0.5, label='初始资金')
        ax1.set_title('回测净值曲线', fontsize=14)
        ax1.set_ylabel('总资产')
        ax1.legend(loc='upper left')
        ax1.grid(alpha=0.3)

        # 标注关键指标
        m = self.metrics
        text = "总收益 %+.2f%% | 年化 %+.2f%% | 夏普 %.2f | 最大回撤 %.2f%%" % (
            m['total_return'] * 100, m['annual_return'] * 100,
            m['sharpe_ratio'], m['max_drawdown'] * 100)
        ax1.text(0.5, 0.02, text, transform=ax1.transAxes, ha='center',
                 fontsize=10, color='gray', bbox=dict(facecolor='white', alpha=0.8))

        # ---- 下: 回撤曲线 ----
        ax2.fill_between(dates, 0, self.drawdown_series * 100,
                         color='#d62728', alpha=0.4, step='mid')
        ax2.plot(dates, self.drawdown_series * 100, color='#d62728',
                 linewidth=0.8, drawstyle='steps')
        ax2.set_title('回撤 (%)', fontsize=12)
        ax2.set_ylabel('回撤 %')
        ax2.set_xlabel('日期')
        ax2.grid(alpha=0.3)

        plt.tight_layout()

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print("[绘图] 已保存: %s" % save_path)

        plt.close(fig)

    def save_trades(self, filepath):
        """保存交易记录为 CSV"""
        if not self.trades:
            print("[交易] 无交易记录")
            return

        df = pd.DataFrame(self.trades)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        print("[交易] 已保存 %d 条记录: %s" % (len(df), filepath))

    def save_metrics(self, filepath):
        """保存绩效指标为 CSV"""
        if not self.metrics:
            return
        df = pd.DataFrame([self.metrics])
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        print("[指标] 已保存: %s" % filepath)
