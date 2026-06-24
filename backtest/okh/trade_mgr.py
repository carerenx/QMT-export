# coding: utf-8
"""
OKH Trade Manager — 订单执行、交易成本计算、持仓跟踪
=====================================================
Adapted from OSkhQuant khTrade.py.
Removes xtquant/xtconstant dependencies; uses local constants.
"""
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# 本地常量 (替代 xtconstant)
# ═══════════════════════════════════════════════════════════════

class OrderType:
    STOCK_BUY = 23
    STOCK_SELL = 24


class PriceType:
    FIX_PRICE = 5          # 限价
    MARKET_PRICE = 1       # 市价


class OrderStatus:
    PENDING = 0
    PARTIALLY_FILLED = 1
    SUCCEEDED = 2
    CANCELLED = 3
    REJECTED = 4


class DirectionFlag:
    LONG = 1
    SHORT = 2


class OffsetFlag:
    OPEN = 1
    CLOSE = 2


# ═══════════════════════════════════════════════════════════════
# 交易管理器
# ═══════════════════════════════════════════════════════════════

class KhTradeManager:
    """交易管理类 — 订单执行、交易成本、持仓跟踪"""

    def __init__(self, config):
        """
        Args:
            config: OkhConfig instance
        """
        self.config = config
        self.orders: Dict[int, dict] = {}
        self.trades: Dict[str, dict] = {}
        self.positions: Dict[str, dict] = {}
        self.assets: Dict[str, float] = {
            'cash': config.init_capital,
            'market_value': 0.0,
            'total_asset': config.init_capital,
            'frozen_cash': 0.0,
        }

        # 交易成本参数
        self.min_commission = config.min_commission
        self.commission_rate = config.commission_rate
        self.stamp_tax_rate = config.stamp_tax_rate
        self.transfer_fee_rate = config.transfer_fee_rate
        self.flow_fee = config.flow_fee
        self.slippage_value = config.slippage_value
        self.slippage_mode = config.slippage_mode
        self.price_decimals = 2
        self.t0_mode = config.t0_mode

        # 订单/成交计数器
        self._order_counter = 0
        self._trade_counter = 0

    # ── 滑点计算 ──

    def calculate_slippage(self, price: float, direction: str) -> float:
        """计算滑点后价格"""
        if self.slippage_mode == 'tick':
            tick_size = 0.01  # A股最小变动价
            slip = tick_size * 2  # 默认2跳
            if direction == 'buy':
                return round(price + slip, self.price_decimals)
            else:
                return round(price - slip, self.price_decimals)
        else:
            # ratio mode
            half_ratio = self.slippage_value / 2
            if direction == 'buy':
                return round(price * (1 + half_ratio), self.price_decimals)
            else:
                return round(price * (1 - half_ratio), self.price_decimals)

    # ── 费用计算 ──

    def calculate_commission(self, price: float, volume: int) -> float:
        """计算佣金 (最低5元)"""
        if volume <= 0:
            return 0.0
        commission = price * volume * self.commission_rate
        return max(commission, self.min_commission)

    def calculate_stamp_tax(self, price: float, volume: int, direction: str) -> float:
        """计算印花税 (仅卖出)"""
        if volume <= 0 or direction != 'sell':
            return 0.0
        return price * volume * self.stamp_tax_rate

    def calculate_transfer_fee(self, stock_code: str, price: float, volume: int) -> float:
        """计算过户费 (仅沪市, 成交金额的十万分之一)"""
        if volume <= 0:
            return 0.0
        # 沪市: .SH 后缀
        if stock_code.endswith('.SH') or stock_code.startswith('6'):
            return price * volume * self.transfer_fee_rate
        return 0.0

    def calculate_trade_cost(self, price: float, volume: int,
                             direction: str, stock_code: str) -> tuple:
        """
        计算完整交易成本

        Returns:
            (actual_price, total_cost) — 滑点后价格，总成本
        """
        if volume <= 0:
            return price, 0.0

        actual_price = self.calculate_slippage(price, direction)
        commission = self.calculate_commission(actual_price, volume)
        stamp_tax = self.calculate_stamp_tax(actual_price, volume, direction)
        transfer_fee = self.calculate_transfer_fee(stock_code, actual_price, volume)
        flow_fee = self.flow_fee

        total_cost = commission + stamp_tax + transfer_fee + flow_fee
        return actual_price, total_cost

    # ── 信号处理 ──

    def process_signals(self, signals: List[dict]):
        """批量处理交易信号

        每个信号:
        {
            'code': str,       # 股票代码
            'action': str,     # 'buy' | 'sell'
            'price': float,    # 委托价格
            'volume': int,     # 委托数量(股)
            'reason': str,     # 交易原因
        }
        """
        for signal in signals:
            if signal.get('volume', 0) <= 0:
                continue
            direction = 'buy' if signal['action'].lower() == 'buy' else 'sell'
            actual_price, trade_cost = self.calculate_trade_cost(
                signal['price'], signal['volume'], direction, signal['code'])
            signal['trade_cost'] = trade_cost
            signal['actual_price'] = actual_price
            self._place_order_backtest(signal)

    # ── 回测下单 ──

    def _place_order_backtest(self, signal: dict):
        """回测模式下单 (假设全部成交)"""
        code = signal['code']
        action = signal['action']
        volume = signal['volume']
        actual_price = signal['actual_price']
        trade_cost = signal['trade_cost']
        decimals = self.price_decimals

        # ── 买入检查：资金是否足够 ──
        if action == 'buy':
            required_cash = actual_price * volume + trade_cost
            if self.assets['cash'] < required_cash:
                print(f"  [资金不足] {code} need={required_cash:.0f} cash={self.assets['cash']:.0f}")
                return

        # ── 卖出检查：持仓是否足够 ──
        if action == 'sell':
            available = self.positions.get(code, {}).get('can_use_volume', 0)
            if available < volume:
                print(f"  [持仓不足] {code} need={volume} available={available}")
                return

        # ── 创建订单记录 ──
        self._order_counter += 1
        order_id = self._order_counter
        order = {
            'order_id': order_id,
            'stock_code': code,
            'order_type': OrderType.STOCK_BUY if action == 'buy' else OrderType.STOCK_SELL,
            'order_volume': volume,
            'traded_volume': volume,   # 回测全部成交
            'price': actual_price,
            'traded_price': actual_price,
            'order_status': OrderStatus.SUCCEEDED,
            'status_msg': signal.get('reason', ''),
            'direction': DirectionFlag.LONG,
            'offset_flag': OffsetFlag.OPEN if action == 'buy' else OffsetFlag.CLOSE,
        }
        self.orders[order_id] = order

        # ── 创建成交记录 ──
        self._trade_counter += 1
        trade_id = f"T{self._trade_counter}"
        trade = {
            'trade_id': trade_id,
            'order_id': order_id,
            'stock_code': code,
            'traded_price': round(actual_price, decimals),
            'traded_volume': volume,
            'traded_amount': round(actual_price * volume, decimals),
            'trade_cost': round(trade_cost, 4),
            'direction': 'buy' if action == 'buy' else 'sell',
            'reason': signal.get('reason', ''),
        }
        self.trades[trade_id] = trade

        # ── 更新资产和持仓 ──
        if action == 'buy':
            self.assets['cash'] -= required_cash
            if code not in self.positions:
                can_use = volume if self.t0_mode else 0
                self.positions[code] = {
                    'stock_code': code,
                    'volume': volume,
                    'can_use_volume': can_use,
                    'avg_price': round(actual_price, decimals),
                    'current_price': round(actual_price, decimals),
                    'market_value': round(actual_price * volume, decimals),
                    'direction': DirectionFlag.LONG,
                }
            else:
                pos = self.positions[code]
                total_value = pos['avg_price'] * pos['volume'] + actual_price * volume
                pos['volume'] += volume
                pos['avg_price'] = round(total_value / pos['volume'], decimals) if pos['volume'] > 0 else 0
                pos['market_value'] = round(pos['volume'] * actual_price, decimals)
                pos['current_price'] = round(actual_price, decimals)
                if self.t0_mode:
                    pos['can_use_volume'] += volume
        else:  # sell
            cash_increase = actual_price * volume - trade_cost
            self.assets['cash'] += cash_increase
            pos = self.positions[code]
            pos['volume'] -= volume
            pos['can_use_volume'] -= volume
            pos['current_price'] = round(actual_price, decimals)
            pos['market_value'] = round(pos['volume'] * actual_price, decimals) if pos['volume'] > 0 else 0
            if pos['volume'] <= 0:
                del self.positions[code]

    # ── 持仓市值更新 ──

    def update_market_values(self, prices: Dict[str, float]):
        """根据最新价格更新所有持仓的市值"""
        total_mv = 0.0
        for code, pos in self.positions.items():
            if code in prices and prices[code] > 0:
                pos['current_price'] = prices[code]
                pos['market_value'] = pos['volume'] * prices[code]
                total_mv += pos['market_value']
        self.assets['market_value'] = total_mv
        self.assets['total_asset'] = self.assets['cash'] + total_mv

    # ── T+1 解锁 ──

    def unlock_t1_positions(self):
        """新交易日：将昨日买入的持仓变为可卖"""
        for code, pos in self.positions.items():
            pos['can_use_volume'] = pos['volume']

    # ── 清仓 ──

    def liquidate_all(self, prices: Dict[str, float], reason: str = '强制清仓') -> List[dict]:
        """按市价清空所有持仓，返回清仓信号列表"""
        signals = []
        for code in list(self.positions.keys()):
            if code in prices and prices[code] > 0:
                vol = self.positions[code]['volume']
                signals.append({
                    'code': code,
                    'action': 'sell',
                    'price': prices[code],
                    'volume': vol,
                    'reason': reason,
                })
        return signals
