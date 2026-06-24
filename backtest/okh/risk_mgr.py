# coding: utf-8
"""
OKH Risk Manager — 风控检查
=============================
Adapted from OSkhQuant khRisk.py.
Adds concrete risk checks: position limit, concentration, max drawdown.
"""
from typing import Dict


class KhRiskManager:
    """风险管理类"""

    def __init__(self, config):
        """
        Args:
            config: OkhConfig instance
        """
        self.config = config
        self.position_limit = config.position_limit   # 仓位上限 (1.0 = 100%, 不限制)
        self.order_limit = config.order_limit          # 单日最大委托数
        self.loss_limit = config.loss_limit            # 止损线 (0.10 = 10%)

        # 运行时状态
        self.peak_asset = config.init_capital          # 历史最高资产
        self.daily_order_count = 0                      # 当日委托计数

    def check_risk(self, data: dict, trade_mgr) -> bool:
        """综合风控检查

        Args:
            data: 当前行情数据
            trade_mgr: KhTradeManager instance

        Returns:
            True = 通过风控, False = 触发风控
        """
        if not self._check_position(trade_mgr):
            return False
        if not self._check_order():
            return False
        if not self._check_max_drawdown(trade_mgr):
            return False
        return True

    def _check_position(self, trade_mgr) -> bool:
        """检查仓位上限"""
        total_asset = trade_mgr.assets['total_asset']
        market_value = trade_mgr.assets['market_value']
        if total_asset > 0 and (market_value / total_asset) > self.position_limit:
            print(f"[风控] 仓位超限: {market_value/total_asset*100:.1f}% > {self.position_limit*100:.1f}%")
            return False
        return True

    def _check_order(self) -> bool:
        """检查单日委托数上限"""
        if self.daily_order_count >= self.order_limit:
            print(f"[风控] 委托数超限: {self.daily_order_count} >= {self.order_limit}")
            return False
        return True

    def _check_max_drawdown(self, trade_mgr) -> bool:
        """检查最大回撤 (每年重置峰值基准，避免永久锁死)"""
        total_asset = trade_mgr.assets['total_asset']

        # 每年重置峰值
        # 注: 此处通过外部 reset_daily 无法获取年份，使用简单逻辑:
        # 回撤恢复后重置 (drawdown < 2% 视为恢复)
        if self.peak_asset > 0:
            current_dd = (self.peak_asset - total_asset) / self.peak_asset
            if current_dd < 0.02:  # 回撤恢复到2%以内
                self.peak_asset = total_asset

        if total_asset > self.peak_asset:
            self.peak_asset = total_asset

        if self.peak_asset > 0:
            drawdown = (self.peak_asset - total_asset) / self.peak_asset
            if drawdown > self.loss_limit:
                print(f"[风控] 回撤超限: {drawdown*100:.1f}% > {self.loss_limit*100:.1f}%")
                return False
        return True

    def reset_daily(self):
        """新交易日：重置日计数器"""
        self.daily_order_count = 0

    def record_order(self):
        """记录一次委托"""
        self.daily_order_count += 1
