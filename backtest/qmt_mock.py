"""
模拟 QMT API 层。

提供 MockContextInfo (模拟 ContextInfo) 和全局函数
(order_shares, get_trade_detail_data, timetag_to_datetime)，
使现有策略代码无需修改即可运行。
"""
import logging
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)


class _ModuleLevelMock:
    """
    持有引擎引用的模块级状态。
    引擎在每次 handlebar 调用之前更新此状态。
    """
    engine = None           # BacktestEngine 引用
    current_date = None     # 当前 bar 的日期


_module_state = _ModuleLevelMock()


# ============================================================
# MockContextInfo
# ============================================================

class MockContextInfo:
    """
    模拟 QMT 的 ContextInfo。

    策略调用方法:
      get_history_data(n, period, field) → {code: [values]}
      get_full_tick([code])             → {code: {lastPrice, ...}}
      get_stock_name(code)              → str or None
      get_bar_timetag(barpos)           → int (ms timestamp)
      set_universe(stock_list)
      get_universe()                    → list
      barpos                            → int
    """

    def __init__(self, data_provider, engine):
        """
        Args:
            data_provider: DataProvider 实例
            engine: BacktestEngine 实例
        """
        self._data = data_provider
        self._engine = engine
        self._barpos = 0
        self.universe = []
        self.stock_pool = []        # 供 get_sector() 使用
        self.acc_id = 'testS'

        # 策略存根属性 (通过 try/except 设置)
        self.capital = 10_000_000
        self.benchmark = '000300.SH'

    @property
    def barpos(self):
        return self._barpos

    @barpos.setter
    def barpos(self, value):
        self._barpos = value

    def set_universe(self, stock_list):
        self.universe = list(stock_list)

    def get_universe(self):
        return self.universe

    def get_history_data(self, length, period, field, dividend_type=0, skip_paused=True):
        """
        模拟 get_history_data。
        返回 {code: [value, value, ...]} — list[0] 最旧, list[-1] 最新。
        """
        if period != '1d':
            logger.warning("仅支持 '1d' 周期, 收到 '%s'", period)
            return {}

        result = {}
        bar = self._barpos
        codes = self.universe if self.universe else self._data.code_list

        # ★ 关键修复: 始终将基准指数加入查询, 否则策略的 _market_ok() 永远返回 True
        benchmark = getattr(self, 'benchmark', None) or '000300.SH'
        if benchmark and benchmark not in codes:
            codes = list(codes) + [benchmark]

        for code in codes:
            values = self._data.get_field_series(code, field)
            if values is None or len(values) == 0:
                continue

            # 取最后 length 个值
            start = max(0, bar - length + 1)
            chunk = values[start:bar + 1]

            # QMT 的 get_history_data 返回纯数值列表, 长度不够也没关系
            if len(chunk) > 0:
                # 确保全部是 float
                result[code] = [float(v) if v is not None else 0.0 for v in chunk]

        return result

    def get_full_tick(self, stock_codes=None):
        """
        模拟 get_full_tick。
        返回 {code: {lastPrice, open, high, low, volume, amount}}
        """
        if stock_codes is None:
            stock_codes = self.universe

        bar = self._barpos
        result = {}
        for code in stock_codes:
            result[code] = {
                'lastPrice': float(self._data.get_value(code, 'close', bar) or 0),
                'open':      float(self._data.get_value(code, 'open', bar) or 0),
                'high':      float(self._data.get_value(code, 'high', bar) or 0),
                'low':       float(self._data.get_value(code, 'low', bar) or 0),
                'volume':    float(self._data.get_value(code, 'volume', bar) or 0),
                'amount':    float(self._data.get_value(code, 'amount', bar) or 0),
            }
        return result

    def get_stock_name(self, code):
        """获取股票名称"""
        return self._data.get_stock_name(code)

    def get_bar_timetag(self, index):
        """
        将 bar 索引转换为毫秒时间戳。
        QMT 返回的是 bar 结束时间的毫秒时间戳 (在日线中是 15:00 CST)。
        """
        dt = self._data.get_date_by_index(index)
        if dt is None:
            return 0
        # 设为 15:00:00 代表收盘时间
        ts = int(dt.timestamp() * 1000)
        return ts

    def get_sector(self, sector_name, real_timetag=-1):
        """
        模拟 get_sector。
        demo-py 版本通过 get_sector('000300.SH') 获取股票池。
        """
        return self.stock_pool or self.universe

    # ---- 存根方法 ----
    def set_slippage(self, *args):
        pass

    def set_commission(self, *args):
        pass

    def set_account(self, acc_id):
        self.acc_id = acc_id

    def is_last_bar(self):
        return True

    def is_new_bar(self):
        return False


# ============================================================
# 模块级模拟函数
# ============================================================

def passorder(opType, orderType, accountid, orderCode, prType, modelprice, volume,
              strategyName='', quickTrade=0, userOrderId='', ContextInfo=None):
    """
    模拟 QMT passorder 函数。
    映射到内部的 order_shares 逻辑。
    """
    # opType: 23=buy, 24=sell (只支持股票买卖)
    if opType == 23:
        direction = 'buy'
    elif opType == 24:
        direction = 'sell'
    else:
        logger.warning("passorder: 不支持的 opType=%d", opType)
        return

    eng = _module_state.engine
    if eng is None:
        logger.warning("passorder: 引擎未连接")
        return

    if volume <= 0:
        return

    # 价格: prType=5(最新价)→None, prType=11(指定价)→modelprice
    price_to_use = None
    if prType == 11:
        price_to_use = modelprice if modelprice > 0 else None

    if price_to_use is None:
        # 用最新价
        tick = ContextInfo.get_full_tick([orderCode]) if ContextInfo else {}
        info = tick.get(orderCode, {})
        price_to_use = info.get('lastPrice', 0)

    if price_to_use <= 0:
        logger.warning("passorder %s: 价格 <= 0", orderCode)
        return

    # volume 单位: orderType 后缀 1=股/手, 支持直接
    shares = int(volume)

    if shares < 100:
        return

    eng._pending_orders.append({
        'code': orderCode,
        'direction': direction,
        'shares': shares,
        'price': price_to_use,
    })


def order_shares(stockcode, shares, style='LATEST', price=None, ContextInfo=None, accId=None):
    """
    模拟 QMT 的 order_shares 函数。
    shares > 0: 买入, shares < 0: 卖出

    QMT 有两种调用约定:
      a) order_shares(code, shares, style, price, ContextInfo, accId)
      b) order_shares(code, shares, style, ContextInfo, accId)  # 无指定价

    策略使用约定 b):
      order_shares(code, shares, 'COMPETE', ContextInfo, State.acc_id)
    """
    # 检测调用约定: 如果 price 参数实际上是 ContextInfo 对象
    if price is not None and not isinstance(price, (int, float)):
        # 约定 b): 参数依次为 code, shares, style, ContextInfo, accId
        accId = ContextInfo
        ContextInfo = price
        price = None

    eng = _module_state.engine
    if eng is None:
        logger.warning("order_shares: 引擎未连接")
        return

    if shares == 0:
        return

    direction = 'buy' if shares > 0 else 'sell'
    abs_shares = abs(shares)

    # 获取当前价格
    if price is not None and price > 0:
        exec_price = price
    else:
        tick = ContextInfo.get_full_tick([stockcode]) if ContextInfo else {}
        info = tick.get(stockcode, {})
        exec_price = info.get('lastPrice', 0)

    if exec_price <= 0:
        logger.warning("order_shares %s: 价格 <= 0 (%.2f)", stockcode, exec_price)
        return

    # 记录到引擎 (按 100 股取整 — 引擎不重复取整)
    eng._pending_orders.append({
        'code': stockcode,
        'direction': direction,
        'shares': abs_shares,
        'price': exec_price,
    })


def get_trade_detail_data(account_id, account_type, data_type):
    """
    模拟 QMT 的 get_trade_detail_data 函数。

    返回模拟 Account 或 Position 对象列表，
    每个对象具有与 QMT C++ 对象相同的属性名。
    """
    eng = _module_state.engine
    if eng is None:
        return []

    data_type = data_type.upper()

    if data_type == 'ACCOUNT':
        return [_MockAccount(eng)]

    elif data_type == 'POSITION':
        return [_MockPosition(code, pos, eng) for code, pos in eng.positions.items()]

    return []


class _MockAccount:
    """模拟 QMT Account 对象"""
    def __init__(self, engine):
        self.m_dAvailable = engine.cash
        self.m_dBalance = engine._calc_total_value()


class _MockPosition:
    """模拟 QMT Position 对象"""
    def __init__(self, code, pos, engine):
        sym, exg = code.split('.')
        self.m_strInstrumentID = sym
        self.m_strExchangeID = exg
        self.m_nVolume = pos['shares']
        self.m_dOpenPrice = pos['entry_price']
        self.m_dLastPrice = engine._get_current_price(code)


def timetag_to_datetime(timetag, fmt='%Y-%m-%d %H:%M'):
    """模拟 QMT 的 timetag_to_datetime 函数"""
    if timetag <= 0:
        return ''
    try:
        return datetime.fromtimestamp(timetag / 1000.0).strftime(fmt)
    except Exception:
        return ''
