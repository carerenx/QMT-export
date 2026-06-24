# coding: utf-8
"""
OKH Reporter — Performance Analysis & Visualization
=====================================================
Enhanced reporting with benchmark comparison, daily stats,
and OSkhQuant-style metrics (Sharpe, max drawdown, win rate, profit factor).
"""
import os
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd


def _setup_chinese_font():
    """设置 matplotlib 中文字体"""
    try:
        import matplotlib
        import matplotlib.font_manager as fm
        zh_fonts = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'Arial Unicode MS']
        for font in zh_fonts:
            try:
                fm.findfont(font, fallback_to_default=False)
                matplotlib.rcParams['font.sans-serif'] = [font]
                matplotlib.rcParams['axes.unicode_minus'] = False
                return True
            except Exception:
                continue
        matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
        return False
    except ImportError:
        return False


class OkhReporter:
    """Enhanced performance reporter with benchmark-relative metrics."""

    def __init__(self, engine, benchmark_code: str = '000905.SH'):
        """
        Args:
            engine: OkhEngine instance (after run())
            benchmark_code: 基准指数代码
        """
        self.engine = engine
        self.benchmark_code = benchmark_code
        self.daily_stats = engine.daily_stats
        self.trades = list(engine.trade_mgr.trades.values())
        self.metrics: dict = {}
        self.df: pd.DataFrame = None
        self.drawdown_series: pd.Series = None

    def calculate(self) -> dict:
        """计算所有绩效指标"""
        if not self.daily_stats:
            print("[报告] 无回测数据")
            return {}

        df = pd.DataFrame(self.daily_stats)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        self.df = df

        # 日收益率
        df['daily_return'] = df['total_asset'].pct_change()
        df['cumulative'] = (1 + df['daily_return']).fillna(1).cumprod()

        # 基准收益率
        if 'benchmark' in df.columns and df['benchmark'].notna().any():
            df['benchmark_return'] = df['benchmark'].pct_change()
            df['benchmark_cum'] = (1 + df['benchmark_return']).fillna(1).cumprod()

        n_days = len(df) - 1
        if n_days < 1:
            return {}

        initial_value = df['total_asset'].iloc[0]
        final_value = df['total_asset'].iloc[-1]
        total_return = final_value / initial_value - 1
        n_years = n_days / 252

        # 年化收益
        ann_return = (final_value / initial_value) ** (1 / n_years) - 1 if n_years > 0 else 0

        # 年化波动率
        daily_ret = df['daily_return'].dropna()
        volatility = float(daily_ret.std() * np.sqrt(252))

        # 夏普比率
        rf = 0.03
        sharpe = (ann_return - rf) / volatility if volatility > 0 else 0

        # 最大回撤
        roll_max = df['cumulative'].cummax()
        drawdown = (df['cumulative'] - roll_max) / roll_max
        max_dd = float(drawdown.min())
        self.drawdown_series = drawdown

        # 最大回撤区间
        dd_end_idx = drawdown.idxmin()
        dd_start_idx = df['cumulative'][:dd_end_idx + 1].idxmax() if dd_end_idx > 0 else 0

        # 基准相对指标
        alpha = 0.0
        beta = 0.0
        info_ratio = 0.0
        if 'benchmark_return' in df.columns:
            bm_ret = df['benchmark_return'].dropna()
            excess = daily_ret - bm_ret.reindex(daily_ret.index)
            excess = excess.dropna()
            if len(excess) > 10:
                ann_excess = (1 + excess).prod() ** (252 / len(excess)) - 1
                alpha = ann_excess
                # Beta
                aligned = pd.DataFrame({'strategy': daily_ret, 'benchmark': bm_ret}).dropna()
                if len(aligned) > 10:
                    cov = aligned.cov().iloc[0, 1]
                    var = aligned['benchmark'].var()
                    beta = cov / var if var > 0 else 0
                    tracking_err = excess.std() * np.sqrt(252)
                    info_ratio = ann_excess / tracking_err if tracking_err > 0 else 0

        # 交易统计
        trade_stats = self._calc_trade_stats()

        # 月度/年度收益
        monthly = df.set_index('date')['total_asset'].resample('ME').last().pct_change().dropna()
        yearly = df.set_index('date')['total_asset'].resample('YE').last().pct_change().dropna()

        # 基准年度收益
        bm_yearly = pd.Series(dtype=float)
        if 'benchmark' in df.columns:
            bm_ts = df.set_index('date')['benchmark'].resample('YE').last().pct_change().dropna()
            bm_yearly = bm_ts

        self.metrics = {
            'start_date': df['date'].iloc[0],
            'end_date': df['date'].iloc[-1],
            'total_days': n_days,
            'initial_value': initial_value,
            'final_value': final_value,
            'total_return': total_return,
            'annual_return': ann_return,
            'volatility': volatility,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'max_drawdown_pct': max_dd * 100,
            'dd_start': df['date'].iloc[dd_start_idx],
            'dd_end': df['date'].iloc[dd_end_idx],
            'alpha': alpha,
            'beta': beta,
            'information_ratio': info_ratio,
            'monthly_returns': monthly,
            'yearly_returns': yearly,
            'benchmark_yearly': bm_yearly,
            **trade_stats,
        }

        return self.metrics

    def _calc_trade_stats(self) -> dict:
        """计算交易统计"""
        trades = self.trades
        if not trades:
            return {
                'total_trades': 0, 'win_rate': 0, 'profit_factor': 0,
                'avg_win': 0, 'avg_loss': 0, 'max_win': 0, 'max_loss': 0,
            }

        # 按成交ID分组计算每笔完整交易的盈亏
        # 简化：直接按买卖对统计
        buys = [t for t in trades if t['direction'] == 'buy']
        sells = [t for t in trades if t['direction'] == 'sell']

        # 匹配买卖对
        pnls = []
        buy_queue = list(buys)
        for sell in sells:
            code = sell['stock_code']
            vol = sell['traded_volume']
            matched_vol = 0
            matched_cost = 0.0
            while matched_vol < vol and buy_queue:
                # Find first buy of same code
                for i, b in enumerate(buy_queue):
                    if b['stock_code'] == code:
                        use_vol = min(vol - matched_vol, b['traded_volume'])
                        matched_cost += use_vol * b['traded_price']
                        matched_vol += use_vol
                        b['traded_volume'] -= use_vol
                        if b['traded_volume'] <= 0:
                            buy_queue.pop(i)
                        break
                else:
                    break
            if matched_vol > 0:
                avg_cost = matched_cost / matched_vol
                pnl = (sell['traded_price'] - avg_cost) * matched_vol - sell.get('trade_cost', 0)
                pnls.append(pnl)

        total_trades = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        profit_factor = (abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0
                        else (float('inf') if wins else 0))

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_win': max(pnls) if pnls else 0,
            'max_loss': min(pnls) if pnls else 0,
        }

    def print_report(self):
        """打印绩效报告"""
        m = self.metrics
        if not m:
            print("[报告] 请先调用 calculate()")
            return

        sep = "=" * 60
        print()
        print(sep)
        print("  OKH 增强回测绩效报告")
        print(sep)
        print(f"  回测区间:  {m['start_date']} ~ {m['end_date']}")
        print(f"  交易天数:  {m['total_days']}")
        print(f"  初始资金:  {m['initial_value']:,.0f}")
        print(f"  最终权益:  {m['final_value']:,.0f}")
        print()
        print("  [收益风险]")
        print(f"    总收益率:       {m['total_return']*100:+.2f}%")
        print(f"    年化收益率:     {m['annual_return']*100:+.2f}%")
        print(f"    年化波动率:     {m['volatility']*100:.2f}%")
        print(f"    夏普比率:       {m['sharpe_ratio']:.2f}")
        print(f"    最大回撤:       {m['max_drawdown']*100:.2f}%")
        print(f"    回撤区间:       {m['dd_start']} ~ {m['dd_end']}")
        print()
        print("  [基准相对]")
        print(f"    Alpha:          {m['alpha']*100:+.2f}%")
        print(f"    Beta:           {m['beta']:.3f}")
        print(f"    信息比率:       {m['information_ratio']:.2f}")
        print()
        print("  [交易统计]")
        print(f"    总交易次数:     {m['total_trades']}")
        print(f"    胜率:           {m['win_rate']*100:.1f}%")
        print(f"    盈亏比:         {m['profit_factor']:.2f}")
        print(f"    平均盈利:       {m['avg_win']:,.0f}")
        print(f"    平均亏损:       {m['avg_loss']:,.0f}")
        print(f"    最大单笔盈利:   {m['max_win']:,.0f}")
        print(f"    最大单笔亏损:   {m['max_loss']:,.0f}")
        print()

        # 年度收益对比
        if hasattr(m['yearly_returns'], 'items') and len(m['yearly_returns']) > 0:
            print("  [年度收益对比]")
            print(f"    {'年份':<6} {'策略':>10} {'基准':>10} {'超额':>10}")
            print(f"    {'-'*36}")
            for yr, ret in m['yearly_returns'].items():
                yr_val = yr if isinstance(yr, int) else yr.year
                bm_ret = m['benchmark_yearly'].get(yr, 0) if hasattr(m['benchmark_yearly'], 'get') else 0
                excess = ret - bm_ret
                print(f"    {yr_val:<6} {ret*100:>9.2f}% {bm_ret*100:>9.2f}% {excess*100:>9.2f}%")
        print()
        print(sep)

    def plot_equity_curve(self, save_path=None):
        """绘制净值曲线 + 回撤图 (含基准对比)"""
        if self.df is None:
            print("[绘图] 无数据")
            return

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            _setup_chinese_font()
        except ImportError:
            print("[绘图] matplotlib 未安装")
            return

        df = self.df
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8),
                                       gridspec_kw={'height_ratios': [3, 1]})

        # 净值曲线
        ax1.plot(df['date'], df['total_asset'], label='策略净值', linewidth=1.5, color='#1f77b4')
        if 'benchmark' in df.columns and df['benchmark'].notna().any():
            # 基准归一化
            bm_norm = df['benchmark'] / df['benchmark'].iloc[df['benchmark'].first_valid_index()] * df['total_asset'].iloc[0]
            ax1.plot(df['date'], bm_norm, label=f'基准({self.benchmark_code})', linewidth=1, color='gray', alpha=0.7)
        ax1.axhline(y=df['total_asset'].iloc[0], color='gray', linestyle='--', alpha=0.3)
        ax1.set_title('回测净值曲线 (OKH Enhanced)', fontsize=14)
        ax1.set_ylabel('总资产')
        ax1.legend(loc='upper left')
        ax1.grid(alpha=0.3)

        # 指标标注
        m = self.metrics
        text = f"总收益 {m['total_return']*100:+.2f}% | 年化 {m['annual_return']*100:+.2f}% | 夏普 {m['sharpe_ratio']:.2f} | 回撤 {m['max_drawdown']*100:.2f}% | Alpha {m['alpha']*100:+.2f}%"
        ax1.text(0.5, 0.02, text, transform=ax1.transAxes, ha='center',
                 fontsize=9, color='gray', bbox=dict(facecolor='white', alpha=0.8))

        # 回撤曲线
        ax2.fill_between(df['date'], 0, self.drawdown_series * 100,
                         color='#d62728', alpha=0.4, step='mid')
        ax2.plot(df['date'], self.drawdown_series * 100, color='#d62728',
                 linewidth=0.8)
        ax2.set_title('回撤 (%)', fontsize=12)
        ax2.set_ylabel('%')
        ax2.set_xlabel('日期')
        ax2.grid(alpha=0.3)

        plt.tight_layout()

        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"[绘图] 已保存: {save_path}")

        plt.close(fig)

    def save_trades(self, filepath: str):
        """保存交易记录 CSV"""
        if not self.trades:
            return
        df = pd.DataFrame(self.trades)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        print(f"[交易] 已保存 {len(df)} 条: {filepath}")

    def save_daily_stats(self, filepath: str):
        """保存每日统计 CSV"""
        if not self.daily_stats:
            return
        df = pd.DataFrame(self.daily_stats)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        print(f"[统计] 已保存 {len(df)} 行: {filepath}")

    def save_metrics(self, filepath: str):
        """保存绩效指标 CSV"""
        if not self.metrics:
            return
        # 排除 Series 字段
        flat = {k: v for k, v in self.metrics.items()
                if not isinstance(v, (pd.Series, pd.DataFrame))}
        df = pd.DataFrame([flat])
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        print(f"[指标] 已保存: {filepath}")
