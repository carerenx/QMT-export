"""
QMT 策略独立回测系统 — 主入口

用法:
    python -m backtest.run
    python -m backtest.run --strategy "MyPy-Q/低回撤多因子趋势跟踪策略.py"
    python -m backtest.run --start 2020-01-01 --end 2024-12-31
    python -m backtest.run --param RISK_PER_TRADE 0.01 --param MAX_POSITIONS 3
"""
import os
import sys
import argparse
import logging
import traceback

# 确保项目根目录在 sys.path 中
BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKTEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def load_strategy_module(strategy_path, mock_globals):
    """
    从文件加载策略模块，注入模拟 QMT 全局变量。

    策略文件中使用了以下 QMT 内置函数:
      order_shares, get_trade_detail_data, timetag_to_datetime
    这些函数在 QMT 运行环境中是全局可用的，但在标准 Python 中不存在。
    我们通过 exec() 注入它们。

    Args:
        strategy_path: 策略 .py 文件路径 (GBK 编码)
        mock_globals:  包含 order_shares, get_trade_detail_data 等的字典

    Returns:
        dict: 策略模块的命名空间 (含 'init', 'handlebar', 'State')
    """
    if not os.path.exists(strategy_path):
        raise FileNotFoundError("策略文件不存在: %s" % strategy_path)

    # 尝试 GBK 编码读取，回退 UTF-8
    for enc in ['gbk', 'utf-8']:
        try:
            with open(strategy_path, 'r', encoding=enc) as f:
                source = f.read()
            break
        except UnicodeDecodeError:
            if enc == 'utf-8':
                raise
            continue

    # 编译源代码
    code = compile(source, strategy_path, 'exec')

    # 构建命名空间
    import numpy as np
    module_globals = {
        '__builtins__': __builtins__,
        '__name__': 'strategy_module',
        '__file__': strategy_path,
        '__doc__': None,
        'np': np,
    }
    # 注入模拟 QMT 全局函数
    module_globals.update(mock_globals)

    # 执行策略文件
    exec(code, module_globals)

    # 验证必要函数存在
    for name in ['init', 'handlebar']:
        if name not in module_globals:
            raise ValueError("策略文件缺少 %s() 函数" % name)

    print("[加载] 策略 %s 加载成功" % os.path.basename(strategy_path))
    return module_globals


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description='QMT 策略独立回测系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m backtest.run
  python -m backtest.run --start 2020-01-01 --end 2024-12-31
  python -m backtest.run --param RISK_PER_TRADE 0.01 --param MAX_POSITIONS 3
  python -m backtest.run --no-plot
        """)

    parser.add_argument('--strategy', default=None,
                        help='策略文件路径 (默认: MyPy-Q/低回撤多因子趋势跟踪策略.py)')
    parser.add_argument('--start', default=None,
                        help='回测开始日期 (默认: 2020-01-01)')
    parser.add_argument('--end', default=None,
                        help='回测结束日期 (默认: 2026-6-17)')
    parser.add_argument('--no-plot', action='store_true',
                        help='跳过生成图表')
    parser.add_argument('--param', nargs=2, action='append', metavar=('KEY', 'VALUE'),
                        help='覆盖策略参数, 如 --param RISK_PER_TRADE 0.01')
    args = parser.parse_args()

    # ---- 1. 确定参数 ----
    strategy_path = args.strategy
    if strategy_path is None:
        strategy_path = config.DEFAULT_STRATEGY
    elif not os.path.isabs(strategy_path):
        strategy_path = os.path.join(PROJECT_ROOT, strategy_path)
    print("strategy_path : ",strategy_path)
    bt_start = args.start or config.BACKTEST_START
    bt_end = args.end or config.BACKTEST_END

    # 参数覆盖
    if args.param:
        for key, val in args.param:
            if hasattr(config, key):
                try:
                    setattr(config, key, eval(val))
                except Exception:
                    setattr(config, key, val)
                print("[配置] %s = %s" % (key, getattr(config, key)))
            else:
                logger.warning("未知配置项: %s", key)

    # ---- 2. 导入模块 ----
    from .data_source import DataProvider
    from .engine import BacktestEngine
    from .analyzer import PerformanceAnalyzer
    from .qmt_mock import (
        MockContextInfo, order_shares, passorder, get_trade_detail_data, timetag_to_datetime
    )

    # ---- 3. 加载数据 ----
    print("[数据] 加载 %d 只股票, %s ~ %s ..." % (
        len(config.STOCK_POOL), bt_start, bt_end))

    codes = config.STOCK_POOL + [config.BENCHMARK_CODE]
    data = DataProvider()
    data.load(codes, bt_start, bt_end)
    data.validate()

    # ---- 4. 创建引擎 ----
    engine = BacktestEngine(data)

    # ---- 5. 加载策略 ----
    mock_globals = {
        'order_shares': order_shares,
        'passorder': passorder,
        'get_trade_detail_data': get_trade_detail_data,
        'timetag_to_datetime': timetag_to_datetime,
    }
    strategy_module = load_strategy_module(strategy_path, mock_globals)

    # ---- 6. 创建 MockContextInfo ----
    context = MockContextInfo(data, engine)
    # ★ 注入股票池, 使 get_sector() 返回已加载的股票列表
    context.stock_pool = config.STOCK_POOL

    # ---- 7. 运行回测 ----
    print("[回测] 开始运行...")
    engine.run(context, strategy_module)

    # ---- 8. 绩效分析 ----
    analyzer = PerformanceAnalyzer(engine)
    analyzer._engine = engine  # 注入引擎引用 (用于基准对比)
    analyzer.calculate()
    analyzer.print_report()

    # ---- 9. 保存结果 ----
    output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    analyzer.save_trades(os.path.join(output_dir, 'trades.csv'))
    analyzer.save_metrics(os.path.join(output_dir, 'metrics.csv'))

    if not args.no_plot:
        analyzer.plot_equity_curve(os.path.join(output_dir, 'equity_curve.png'))

    print("[完成] 结果已保存至 %s" % output_dir)


if __name__ == '__main__':
    try:
        # 延迟 import config 确保 sys.path 已设置
        from . import config
        main()
    except Exception as e:
        print("=" * 50)
        print("回测异常: %s" % e)
        traceback.print_exc()
        sys.exit(1)
