"""
OKH Enhanced Backtest — CLI Entry Point
=========================================
Runs QMT-format strategies on the OSkhQuant-adapted enhanced engine.

用法:
    python -m backtest.run_okh
    python -m backtest.run_okh --strategy "MyPy-Q/Alpha144_流动性冲击择时策略.py"
    python -m backtest.run_okh --start 2020-01-01 --end 2025-12-31
    python -m backtest.run_okh --config config.json --no-plot
"""
import os
import sys
import argparse
import logging
import traceback
from datetime import datetime

# 确保项目根目录在 sys.path 中
BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKTEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='OKH Enhanced 回测系统 (OSkhQuant 引擎)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m backtest.run_okh
  python -m backtest.run_okh --strategy "MyPy-Q/Alpha144_流动性冲击择时策略.py"
  python -m backtest.run_okh --start 2020-01-01 --end 2025-12-31
  python -m backtest.run_okh --config my_config.json --no-plot
        """)

    parser.add_argument('--strategy', default=None,
                        help='策略文件路径')
    parser.add_argument('--start', default=None,
                        help='回测开始日期 (默认: 2020-01-01)')
    parser.add_argument('--end', default=None,
                        help='回测结束日期')
    parser.add_argument('--config', default=None,
                        help='OKH JSON 配置文件路径 (.kh 格式)')
    parser.add_argument('--no-plot', action='store_true',
                        help='跳过生成图表')
    args = parser.parse_args()

    # ── 导入模块 ──
    from backtest.data_source import DataProvider
    from backtest.okh.config import OkhConfig, create_default_config
    from backtest.okh.engine import OkhEngine
    from backtest.okh.adapter import QmtStrategyAdapter
    from backtest.okh.reporter import OkhReporter
    from backtest import config as bt_config

    # ── 1. 确定回测参数 ──
    strategy_path = args.strategy
    if strategy_path is None:
        strategy_path = bt_config.DEFAULT_STRATEGY
    elif not os.path.isabs(strategy_path):
        strategy_path = os.path.join(PROJECT_ROOT, strategy_path)

    bt_start = args.start or bt_config.BACKTEST_START
    bt_end = args.end or bt_config.BACKTEST_END

    # ── 2. 加载配置 ──
    if args.config:
        okh_config = OkhConfig(args.config)
    else:
        okh_config = create_default_config(
            stock_pool=bt_config.STOCK_POOL,
            benchmark=bt_config.BENCHMARK_CODE,
            start=bt_start.replace('-', ''),
            end=bt_end.replace('-', ''),
            capital=bt_config.INITIAL_CAPITAL,
        )

    print(f"[配置] 回测区间: {bt_start} ~ {bt_end}")
    print(f"[配置] 股票池: {len(okh_config.stock_pool)} 只")
    print(f"[配置] 初始资金: {okh_config.init_capital:,.0f}")
    print(f"[配置] T+0模式: {okh_config.t0_mode}")

    # ── 3. 加载数据 ──
    codes = okh_config.stock_pool + [okh_config.benchmark_code]
    print(f"[数据] 加载 {len(codes)} 只股票...")
    data = DataProvider()
    data.load(list(set(codes)), bt_start, bt_end)
    data.validate()

    # ── 4. 创建引擎 ──
    engine = OkhEngine(data, okh_config)

    # ── 5. 加载并适配策略 ──
    adapter = QmtStrategyAdapter(strategy_path, data, okh_config)
    strategy_mod = adapter.load()

    # ── 6. 连接适配器→引擎 (供 get_trade_detail_data 路由) ──
    adapter._engine_ref = engine

    # ── 7. 运行回测 ──
    print("[回测] 开始运行...")
    engine.run(strategy_mod)

    # ── 7. 绩效分析 ──
    reporter = OkhReporter(engine, okh_config.benchmark_code)
    reporter.calculate()
    reporter.print_report()

    # ── 8. 保存结果 ──
    output_dir = os.path.join(
        BACKTEST_DIR, 'output',
        f"okh_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    os.makedirs(output_dir, exist_ok=True)

    reporter.save_trades(os.path.join(output_dir, 'trades.csv'))
    reporter.save_daily_stats(os.path.join(output_dir, 'daily_stats.csv'))
    reporter.save_metrics(os.path.join(output_dir, 'metrics.csv'))

    if not args.no_plot:
        reporter.plot_equity_curve(os.path.join(output_dir, 'equity_curve.png'))

    print(f"\n[完成] 结果已保存至 {output_dir}")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print("=" * 60)
        print(f"回测异常: {e}")
        traceback.print_exc()
        sys.exit(1)
