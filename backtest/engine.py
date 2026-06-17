"""
回测引擎

逐日迭代，调用策略的 init() 和 handlebar()，处理订单，维护投资组合状态。
"""
import os
import logging
from datetime import datetime

import numpy as np

from . import config
from . import qmt_mock

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    回测引擎主类。

    关键状态:
      cash: float — 当前可用现金
      positions: {code: {shares, entry_price, ...}} — 当前持仓
      trades: [trade_dict, ...] — 历史交易记录
      equity_curve: [(date, total_value), ...] — 每日净值序列
    """

    def __init__(self, data_provider):
        self.data = data_provider
        self.cash = float(config.INITIAL_CAPITAL)
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self._pending_orders = []
        self._current_bar_date = None

        # 性能统计 (累计)
        self._total_buy_amount = 0.0
        self._total_sell_amount = 0.0
        self._total_commission = 0.0
        self._total_stamp_tax = 0.0

    def _reset(self):
        """重置引擎状态（每次 run 前调用）"""
        self.cash = float(config.INITIAL_CAPITAL)
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        self._pending_orders = []
        self._current_bar_date = None
        self._total_buy_amount = 0.0
        self._total_sell_amount = 0.0
        self._total_commission = 0.0
        self._total_stamp_tax = 0.0

    def _calc_total_value(self):
        """计算总资产 = 现金 + 持仓市值"""
        pos_value = 0.0
        for code, pos in self.positions.items():
            px = self._get_current_price(code)
            pos_value += pos['shares'] * px
        return self.cash + pos_value

    def _get_current_price(self, code):
        """获取某只股票的当前价格"""
        bar = self._current_bar
        return float(self.data.get_value(code, 'close', bar) or 0)

    def run(self, context, strategy_module):
        """
        运行回测。

        Args:
            context: MockContextInfo 实例
            strategy_module: exec() 加载的策略模块命名空间 dict (含 'init', 'handlebar', 'State')
        """
        self._reset()

        init_fn = strategy_module.get('init')
        handlebar_fn = strategy_module.get('handlebar')
        State = strategy_module.get('State')

        if init_fn is None or handlebar_fn is None:
            raise ValueError("策略模块缺少 init() 或 handlebar() 函数")

        # ---- 连接 module-level mock ----
        qmt_mock._module_state.engine = self

        # ====== 调用 init() ======
        print("[引擎] 调用 init() ...")
        try:
            init_fn(context)
        except Exception as e:
            logger.error("init() 异常: %s", e)
            import traceback
            traceback.print_exc()
            raise

        # ====== 逐日回测 ======
        n_bars = len(self.data.dates_list)
        last_print_pct = 0
        ma_long = getattr(config, 'MA_LONG', 60)

        for bar in range(n_bars):
            # 进度打印
            pct = int((bar + 1) / n_bars * 100)
            if pct >= last_print_pct + 10:
                print("[引擎] 进度 %d%% (%d/%d)" % (pct, bar + 1, n_bars))
                last_print_pct = pct

            # ---- 更新当前 bar ----
            current_date = self.data.dates_list[bar]
            self._current_bar = bar
            self._current_bar_date = current_date
            context.barpos = bar

            # ---- 更新模块级 mock 状态 ----
            qmt_mock._module_state.current_date = current_date

            # ---- 清空上根 bar 的待处理订单 ----
            self._pending_orders = []

            # ---- 调用 handlebar() ----
            try:
                handlebar_fn(context)
            except Exception as e:
                logger.error("bar=%d %s handlebar() 异常: %s",
                             bar, current_date.strftime('%Y-%m-%d'), e)
                import traceback
                traceback.print_exc()
                continue

            # ---- 处理订单 ----
            self._process_orders(context)

            # ---- 记录净值 ----
            total_value = self._calc_total_value()
            self.equity_curve.append({
                'date': current_date,
                'total_value': total_value,
                'cash': self.cash,
                'positions_value': total_value - self.cash,
            })

        # ---- 回测结束, 强制平仓 ----
        self._liquidate_all(context)

        print("[引擎] 回测完成: %d 个交易日, %d 笔交易" % (
            n_bars, len(self.trades)))
        return self

    def _process_orders(self, context):
        """处理所有挂单"""
        # 先处理卖出, 再处理买入, 避免现金不足
        sells = [o for o in self._pending_orders if o['direction'] == 'sell']
        buys = [o for o in self._pending_orders if o['direction'] == 'buy']

        for order in sells:
            self._execute_sell(order, context)

        for order in buys:
            self._execute_buy(order, context)

    def _execute_sell(self, order, context):
        """执行卖出订单"""
        code = order['code']
        shares = order['shares']
        price = order['price']

        # 检查持仓是否足够
        if code not in self.positions:
            return
        actual_shares = self.positions[code]['shares']
        sell_shares = min(shares, actual_shares)

        # 滑点: 卖出价下调
        exec_price = price * (1 - config.SLIPPAGE)
        amount = sell_shares * exec_price
        commission = amount * config.COMMISSION_RATE
        stamp_tax = amount * config.STAMP_TAX_RATE
        net_amount = amount - commission - stamp_tax

        self.cash += net_amount
        self._total_sell_amount += amount
        self._total_commission += commission
        self._total_stamp_tax += stamp_tax

        # 记录交易
        entry_price = self.positions[code].get('entry_price', exec_price)
        pnl = (exec_price - entry_price) * sell_shares - commission - stamp_tax

        self.trades.append({
            'code': code,
            'date': self._current_bar_date,
            'direction': 'sell',
            'shares': sell_shares,
            'price': round(exec_price, 3),
            'commission': round(commission, 2),
            'stamp_tax': round(stamp_tax, 2),
            'pnl': round(pnl, 2),
        })

        # 更新或移除持仓
        if sell_shares >= actual_shares:
            del self.positions[code]
        else:
            self.positions[code]['shares'] -= sell_shares

    def _execute_buy(self, order, context):
        """执行买入订单"""
        code = order['code']
        shares = order['shares']
        price = order['price']

        # 滑点: 买入价上调
        exec_price = price * (1 + config.SLIPPAGE)
        amount = shares * exec_price
        commission = amount * config.COMMISSION_RATE

        # 检查现金是否足够
        total_cost = amount + commission
        if total_cost > self.cash * 0.99:
            # 现金不足, 按比例减少股数
            max_amount = self.cash * 0.99
            shares = int(max_amount / (exec_price * (1 + config.COMMISSION_RATE)) / 100) * 100
            if shares < 100:
                return
            amount = shares * exec_price
            commission = amount * config.COMMISSION_RATE
            total_cost = amount + commission

        self.cash -= total_cost
        self._total_buy_amount += amount
        self._total_commission += commission

        # 记录交易
        self.trades.append({
            'code': code,
            'date': self._current_bar_date,
            'direction': 'buy',
            'shares': shares,
            'price': round(exec_price, 3),
            'commission': round(commission, 2),
            'stamp_tax': 0,
            'pnl': 0,
        })

        # 更新或创建持仓
        if code in self.positions:
            # 加权平均成本
            old = self.positions[code]
            old_shares = old['shares']
            old_cost = old_shares * old['entry_price']
            new_cost = shares * exec_price
            old['shares'] = old_shares + shares
            old['entry_price'] = (old_cost + new_cost) / (old_shares + shares)
        else:
            self.positions[code] = {
                'shares': shares,
                'entry_price': exec_price,
                'bars_held': 0,
                'highest': exec_price,
                'atr': 0,
            }

    def _liquidate_all(self, context):
        """回测结束时强制平仓"""
        if not self.positions:
            return
        codes = list(self.positions.keys())
        for code in codes:
            pos = self.positions[code]
            price = self._get_current_price(code)
            if price <= 0:
                continue
            exec_price = price * (1 - config.SLIPPAGE)
            amount = pos['shares'] * exec_price
            commission = amount * config.COMMISSION_RATE
            stamp_tax = amount * config.STAMP_TAX_RATE
            net_amount = amount - commission - stamp_tax
            self.cash += net_amount

            # 记录平仓交易
            pnl = (exec_price - pos['entry_price']) * pos['shares'] - commission - stamp_tax
            self.trades.append({
                'code': code,
                'date': self._current_bar_date,
                'direction': 'liquidate',
                'shares': pos['shares'],
                'price': round(exec_price, 3),
                'commission': round(commission, 2),
                'stamp_tax': round(stamp_tax, 2),
                'pnl': round(pnl, 2),
            })

        self.positions = {}
        print("[引擎] 强制平仓完成")
