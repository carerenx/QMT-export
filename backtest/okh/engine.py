# coding: utf-8
"""
OKH Engine — Enhanced Event-Driven Backtest Loop
=================================================
Adapted from OSkhQuant khFrame.py core backtest loop.
Uses existing DataProvider instead of xtquant.
Adds T+1 tracking, pre/post-market hooks, trigger system.
"""
from typing import Dict, List, Optional
import datetime

import numpy as np
import pandas as pd

from .config import OkhConfig, create_default_config
from .trade_mgr import KhTradeManager
from .risk_mgr import KhRiskManager


class OkhEngine:
    """
    增强回测引擎 — 支持 T+1 锁定、盘前/盘后回调、日K线触发器。
    """

    def __init__(self, data_provider, config: OkhConfig = None):
        """
        Args:
            data_provider: DataProvider instance (from backtest.data_source)
            config: OkhConfig instance (created from JSON or programmatically)
        """
        self.data = data_provider
        self.config = config
        self.trade_mgr = KhTradeManager(config) if config else None
        self.risk_mgr = KhRiskManager(config) if config else None

        # ── 回测结果 ──
        self.daily_stats: List[dict] = []
        self.benchmark_data: List[dict] = []

        # ── 运行时状态 ──
        self._current_bar = 0
        self._current_date = None
        self._current_prices = {}
        self._last_trigger_date = None
        self._pre_market_called_today = False
        self._post_market_called_today = False

    def run(self, strategy_module: dict) -> 'OkhEngine':
        """运行回测

        Args:
            strategy_module: dict with 'init', 'khHandlebar' keys
                            (and optionally 'khPreMarket', 'khPostMarket')

        Returns:
            self (for chaining with reporter)
        """
        if self.trade_mgr is None:
            raise RuntimeError("Engine not configured. Pass an OkhConfig to __init__.")

        init_fn = strategy_module.get('init')
        handlebar_fn = strategy_module.get('khHandlebar')
        pre_market_fn = strategy_module.get('khPreMarket')
        post_market_fn = strategy_module.get('khPostMarket')

        if handlebar_fn is None:
            raise ValueError("Strategy module missing khHandlebar()")

        n_bars = len(self.data.dates_list)
        stock_codes = list(self.data.code_list)
        benchmark_code = self.config.benchmark_code

        # ── init() ──
        print("[OKH引擎] 调用 init() ...")
        if init_fn:
            all_data = self._build_full_data_dict()
            init_fn(stocks=stock_codes, data=all_data)

        # ── 逐日回测 ──
        last_print_pct = 0
        for bar in range(n_bars):
            pct = int((bar + 1) / n_bars * 100)
            if pct >= last_print_pct + 10:
                print("[OKH引擎] 进度 %d%% (%d/%d) | 持仓=%d | 资产=%.0f万" % (
                    pct, bar + 1, n_bars,
                    len(self.trade_mgr.positions),
                    self.trade_mgr.assets['total_asset'] / 10000))
                last_print_pct = pct

            self._current_bar = bar
            current_date = self.data.dates_list[bar]
            self._current_date = current_date
            self._current_prices = self._get_current_prices()

            # ── 更新持仓市值 ──
            self.trade_mgr.update_market_values(self._current_prices)

            # ── 日切换逻辑 ──
            is_new_day = self._is_new_trading_day(current_date)
            if is_new_day:
                # 盘前: T+1 解锁
                self.trade_mgr.unlock_t1_positions()
                self.risk_mgr.reset_daily()
                self._pre_market_called_today = False
                self._post_market_called_today = False

                # 盘前回调
                if pre_market_fn and not self._pre_market_called_today:
                    data = self._build_bar_data_dict()
                    try:
                        pre_market_fn(data)
                    except Exception as e:
                        print(f"[OKH引擎] khPreMarket 异常: {e}")
                    self._pre_market_called_today = True

            # ── 日K线触发 (每个交易日触发一次) ──
            if self._should_trigger_daily(current_date):
                # 调用策略 (策略自行管理风险，引擎不干预)
                data = self._build_bar_data_dict()
                try:
                    signals = handlebar_fn(data)
                    if signals:
                        self.trade_mgr.process_signals(signals)
                except Exception as e:
                    print(f"[OKH引擎] khHandlebar 异常 @ {current_date}: {e}")
                    import traceback
                    traceback.print_exc()

            # ── 盘后回调 ──
            if is_new_day and post_market_fn and not self._post_market_called_today:
                data = self._build_bar_data_dict()
                try:
                    post_market_fn(data)
                except Exception as e:
                    print(f"[OKH引擎] khPostMarket 异常: {e}")
                self._post_market_called_today = True

            # ── 记录每日统计 ──
            self._record_daily_stats(current_date, benchmark_code)

        # ── 回测结束强制平仓 ──
        self._liquidate_all()

        print("[OKH引擎] 回测完成: %d 个交易日 | 持仓=%d | 终值=%.0f万" % (
            n_bars, len(self.trade_mgr.positions),
            self.trade_mgr.assets['total_asset'] / 10000))
        return self

    # ── 内部方法 ──

    def _get_current_prices(self) -> Dict[str, float]:
        """获取当前 bar 所有股票的收盘价"""
        prices = {}
        bar = self._current_bar
        for code in self.data.code_list:
            px = self.data.get_value(code, 'close', bar)
            if px and px > 0:
                prices[code] = float(px)
        return prices

    def _is_new_trading_day(self, current_date) -> bool:
        """判断是否为新的交易日 (日K线模式首日也算新日)"""
        if self._last_trigger_date is None:
            return True
        if isinstance(current_date, datetime.datetime):
            return current_date.date() != self._last_trigger_date.date() if hasattr(self._last_trigger_date, 'date') else True
        return current_date != self._last_trigger_date

    def _should_trigger_daily(self, current_date) -> bool:
        """日K线触发：每个交易日触发一次"""
        if self._last_trigger_date is None:
            self._last_trigger_date = current_date
            return True
        if isinstance(current_date, datetime.datetime):
            if current_date.date() != self._last_trigger_date.date() if hasattr(self._last_trigger_date, 'date') else False:
                self._last_trigger_date = current_date
                return True
        elif current_date != self._last_trigger_date:
            self._last_trigger_date = current_date
            return True
        return False

    def _build_bar_data_dict(self) -> dict:
        """构建当前 bar 的数据字典（OSkhQuant 策略接口格式）"""
        data = {
            '__current_time__': self._current_date,
            '__account__': self.trade_mgr.assets,
            '__positions__': self.trade_mgr.positions,
            '__stock_list__': list(self.data.code_list),
            '__framework__': self,
        }
        # 每只股票当前数据
        bar = self._current_bar
        for code in self.data.code_list:
            row = {}
            for field in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                val = self.data.get_value(code, field, bar)
                row[field] = float(val) if val else 0.0
            data[code] = pd.Series(row)
        return data

    def _build_full_data_dict(self) -> dict:
        """构建完整历史数据字典 (供 init 使用)"""
        return self._build_bar_data_dict()

    def _record_daily_stats(self, current_date, benchmark_code: str):
        """记录每日统计"""
        assets = self.trade_mgr.assets
        # 基准指数值
        benchmark_val = None
        if benchmark_code and benchmark_code in self.data.code_list:
            bar = self._current_bar
            benchmark_val = self.data.get_value(benchmark_code, 'close', bar)

        stats = {
            'date': current_date,
            'total_asset': assets['total_asset'],
            'cash': assets['cash'],
            'market_value': assets['market_value'],
            'positions_count': len(self.trade_mgr.positions),
            'benchmark': benchmark_val,
        }
        self.daily_stats.append(stats)

    def _liquidate_all(self):
        """强制清仓"""
        if not self.trade_mgr.positions:
            return
        signals = self.trade_mgr.liquidate_all(self._current_prices, '回测结束强制平仓')
        if signals:
            self.trade_mgr.process_signals(signals)
        print("[OKH引擎] 强制平仓完成")
