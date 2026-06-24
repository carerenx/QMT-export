# coding: utf-8
"""
OKH Configuration Management
==============================
JSON-based configuration compatible with OSkhQuant .kh format.
Falls back to existing backtest/config.py values when no JSON config provided.
"""
import json
import os
from datetime import datetime


class OkhConfig:
    """配置管理类 — 兼容 OSkhQuant .kh 配置文件格式"""

    def __init__(self, config_path=None):
        """
        Args:
            config_path: .kh JSON 配置文件路径，为 None 时使用默认配置
        """
        self.config_dict = {}

        if config_path and os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config_dict = json.load(f)

        # ── 系统配置 ──
        system = self.config_dict.get('system', {})
        self.run_mode = system.get('run_mode', 'backtest')
        self.session_id = system.get('session_id', int(datetime.now().timestamp()))

        # ── 账户配置 ──
        account = self.config_dict.get('account', {})
        self.account_id = account.get('account_id', 'test_account')
        self.account_type = account.get('account_type', 'SECURITY_ACCOUNT')

        # ── 回测配置 ──
        backtest = self.config_dict.get('backtest', {})
        self.backtest_start = backtest.get('start_time', '20200101')
        self.backtest_end = backtest.get('end_time', '20251231')
        self.init_capital = backtest.get('init_capital', 10_000_000)

        # 交易成本
        trade_cost = backtest.get('trade_cost', {})
        self.commission_rate = trade_cost.get('commission_rate', 0.00025)   # 万2.5
        self.min_commission = trade_cost.get('min_commission', 5.0)          # 最低5元
        self.stamp_tax_rate = trade_cost.get('stamp_tax_rate', 0.001)        # 千1(卖)
        self.transfer_fee_rate = trade_cost.get('transfer_fee_rate', 0.00001) # 十万1
        self.flow_fee = trade_cost.get('flow_fee', 0.1)                      # 流量费0.1元
        self.slippage_mode = trade_cost.get('slippage_mode', 'ratio')
        self.slippage_value = trade_cost.get('slippage_value', 0.001)        # 千1滑点

        # T+0/T+1 模式
        self.t0_mode = backtest.get('t0_mode', False)

        # ── 数据配置 ──
        data = self.config_dict.get('data', {})
        self.kline_period = data.get('kline_period', '1d')
        self.stock_pool = data.get('stock_list', data.get('stock_pool', []))

        # 基准指数
        self.benchmark_code = data.get('benchmark', '000905.SH')

        # ── 风控配置 ──
        risk = self.config_dict.get('risk', {})
        self.position_limit = risk.get('position_limit', 0.95)
        self.order_limit = risk.get('order_limit', 100)
        self.loss_limit = risk.get('loss_limit', 0.10)

        # ── 触发配置 ──
        trigger = self.config_dict.get('trigger', {})
        self.trigger_type = trigger.get('type', 'kline')    # kline, tick, custom
        self.trigger_period = trigger.get('period', '1d')   # 1d, 1m, 5m
        self.trigger_times = trigger.get('custom_times', []) # for custom trigger

    @property
    def initial_cash(self):
        """获取初始资金"""
        return self.init_capital

    def get_stock_list(self):
        """获取股票列表"""
        data = self.config_dict.get('data', {})
        return data.get('stock_list', data.get('stock_pool', []))

    def to_dict(self):
        """导出为字典"""
        return {
            'system': {
                'run_mode': self.run_mode,
                'session_id': self.session_id,
            },
            'account': {
                'account_id': self.account_id,
                'account_type': self.account_type,
            },
            'backtest': {
                'start_time': self.backtest_start,
                'end_time': self.backtest_end,
                'init_capital': self.init_capital,
                'trade_cost': {
                    'commission_rate': self.commission_rate,
                    'min_commission': self.min_commission,
                    'stamp_tax_rate': self.stamp_tax_rate,
                    'transfer_fee_rate': self.transfer_fee_rate,
                    'flow_fee': self.flow_fee,
                    'slippage_mode': self.slippage_mode,
                    'slippage_value': self.slippage_value,
                },
                't0_mode': self.t0_mode,
            },
            'data': {
                'kline_period': self.kline_period,
                'stock_list': self.stock_pool,
                'benchmark': self.benchmark_code,
            },
            'risk': {
                'position_limit': self.position_limit,
                'order_limit': self.order_limit,
                'loss_limit': self.loss_limit,
            },
            'trigger': {
                'type': self.trigger_type,
                'period': self.trigger_period,
            },
        }


def create_default_config(stock_pool, benchmark='000905.SH',
                          start='20200101', end='20251231',
                          capital=10_000_000):
    """创建默认配置（编程方式，无需 JSON 文件）

    Args:
        stock_pool: 股票代码列表
        benchmark: 基准指数代码
        start: 回测开始日期 (YYYYMMDD)
        end: 回测结束日期 (YYYYMMDD)
        capital: 初始资金

    Returns:
        OkhConfig instance
    """
    config = OkhConfig()
    config.backtest_start = start
    config.backtest_end = end
    config.init_capital = capital
    config.stock_pool = stock_pool
    config.benchmark_code = benchmark
    return config
