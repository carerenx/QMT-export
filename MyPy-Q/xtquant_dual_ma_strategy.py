# -*- coding: utf-8 -*-
"""
================================================================================
 MiniQMT + xtquant 双均线趋势跟踪策略 v1.0
================================================================================
 直接在 VS Code 中运行，通过 xtquant 连接 MiniQMT 实现实盘自动交易。

 【架构】
   VS Code (Python脚本)
     ├─ xtdata   → 连接 MiniQMT 获取行情数据
     ├─ xttrader → 连接 MiniQMT 执行交易/查询持仓
     └─ 策略逻辑 → 均线金叉/死叉 + 风控

 【与 QMT内置策略 的区别】
   QMT内置:   策略在QMT客户端内运行, 用 ContextInfo/handlebar 等注入函数
   MiniQMT:   策略在 VS Code 运行, 用 xtdata/xttrader 直接调用, 完整的IDE支持
              (代码补全、断点调试、git版本管理等)

 【前置条件】
   1. 安装 QMT 客户端 (已安装则跳过)
   2. 启动 MiniQMT (极简模式):
      - 打开 QMT → 右上角"极简模式"按钮
      - 或命令行: QMT.exe --mini
      - MiniQMT 默认监听端口 58610
   3. pip install xtquant (已完成)

 【运行方式 — 三种模式】

   模式1: 回测模式 (纯本地, 不需要MiniQMT)
     python xtquant_dual_ma_strategy.py --mode backtest

   模式2: 实时信号模式 (连接MiniQMT, 只看信号不下单)
     python xtquant_dual_ma_strategy.py --mode signal

   模式3: 实盘模式 (连接MiniQMT, 自动下单)
     python xtquant_dual_ma_strategy.py --mode live

 【参数配置】
   见下方 CONFIG 区域，所有参数集中管理。

================================================================================
"""
import os
import sys
import time
import argparse
import traceback
from datetime import datetime, timedelta
from collections import deque
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd

from xtquant import xtdata, xttrader, xtconstant
import xtquant  # 用于版本号

# ============================================================================
# ★ 用户配置区 — 修改参数在这里 ★
# ============================================================================

class Config:
    """策略配置 (集中管理, 方便修改)"""

    # ── 交易标的 ──
    STOCK_CODE = '601869'           # 股票代码
    STOCK_NAME = '长飞光纤'
    MARKET = 'SH'                   # SH=上证, SZ=深证

    # ── MiniQMT连接 ──
    # MiniQMT userdata 目录 (QMT安装目录下的userdata_mini文件夹)
    # 常见路径: C:/QMT/userdata_mini
    MINIQMT_PATH = 'C:/QMT/userdata_mini'
    SESSION_ID = 0                  # 会话ID (整数, 每次连接递增)
    ACCOUNT_ID = '8890145315'       # 资金账号

    # ── 均线参数 ──
    FAST_MA = 5                     # 快线周期
    SLOW_MA = 20                    # 慢线周期
    TREND_MA = 60                   # 趋势过滤均线
    DATA_DAYS = 200                 # 获取历史数据天数

    # ── 交易参数 ──
    TRADE_LOT = 100                 # 每手股数 (A股=100)
    MAX_LOTS = 5                    # 最大持仓手数
    ORDER_TYPE = xtconstant.FIX_PRICE  # 下单价格类型

    # ── 风控参数 ──
    STOP_LOSS_PCT = 0.05            # 止损 -5%
    TAKE_PROFIT_PCT = 0.15          # 止盈 +15%
    TRAILING_STOP_PCT = 0.08        # 回撤止盈 8%

    # ── 运行参数 ──
    CHECK_INTERVAL_SEC = 3          # 轮询间隔 (建议≥3秒)
    HEARTBEAT_MIN = 5               # 心跳日志间隔(分钟)

    # ── 开关 ──
    DRY_RUN = False                 # True=只发信号不下单(安全模式)
    ENABLE_VOLUME_FILTER = True     # 成交量过滤
    VOLUME_RATIO = 1.2              # 量比阈值

    @property
    def stock_qmt(self) -> str:
        return f'{self.STOCK_CODE}.{self.MARKET}'

    @property
    def stock_xt(self) -> str:
        """xtquant 格式: 上海=代码.SH, 深圳=代码.SZ"""
        return f'{self.STOCK_CODE}.{self.MARKET}'


# ============================================================================
# 行情数据模块 (xtdata)
# ============================================================================

class MarketData:
    """封装 xtdata 行情数据获取"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._connected = False
        self._tick_cache = {}

    def connect(self) -> bool:
        """连接 MiniQMT 行情服务"""
        try:
            # xtdata.connect() 连接到 MiniQMT
            # 如果 MiniQMT 未启动, 此调用会失败
            xtdata.connect()
            self._connected = True
            print(f'[行情] 已连接 MiniQMT 行情服务')
            return True
        except Exception as e:
            print(f'[行情] 连接失败: {e}')
            print(f'[提示] 请先启动 MiniQMT (QMT右上角"极简模式"按钮)')
            return False

    def disconnect(self):
        """断开行情连接"""
        if self._connected:
            xtdata.disconnect()
            self._connected = False

    def download_history(self) -> pd.DataFrame:
        """
        获取历史K线数据。

        优先用 xtdata (需要MiniQMT在线), 失败则用 baostock (纯本地, 最可靠).

        返回: DataFrame with columns [open, high, low, close, volume]
        """
        # ── 方式1: xtdata (连接MiniQMT时可用) ──
        if self._connected:
            df = self._download_via_xtdata()
            if not df.empty:
                return df
            print('[数据] xtdata 无数据, 尝试 baostock fallback...')

        # ── 方式2: baostock fallback (纯本地, 不需要MiniQMT) ──
        return self._download_via_baostock()

    def _download_via_xtdata(self) -> pd.DataFrame:
        """通过 xtdata 获取历史K线 (需要MiniQMT在线)"""
        code = self.cfg.stock_xt
        period = '1d'
        end = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=self.cfg.DATA_DAYS)).strftime('%Y%m%d')

        print(f'[数据:xtdata] 下载 {code} {period} {start}-{end} ...')

        try:
            xtdata.download_history_data(code, period, start, end)
            data = xtdata.get_market_data(
                field_list=['open', 'high', 'low', 'close', 'volume'],
                stock_list=[code],
                period=period,
                count=self.cfg.DATA_DAYS,
                dividend_type='front_ratio',
                fill_data=True,
            )

            if code not in data.get('close', {}) or len(data['close'][code]) == 0:
                return pd.DataFrame()

            df = pd.DataFrame({
                'open': data['open'][code],
                'high': data['high'][code],
                'low': data['low'][code],
                'close': data['close'][code],
                'volume': data['volume'][code],
            })
            df = df[(df['close'] > 0) & (df['volume'] > 0)]
            print(f'[数据:xtdata] 获取 {len(df)} 条有效日线')
            return df
        except Exception as e:
            print(f'[数据:xtdata] 失败: {e}')
            return pd.DataFrame()

    def _download_via_baostock(self) -> pd.DataFrame:
        """通过 baostock 获取历史K线 (纯本地, 无需MiniQMT)"""
        try:
            import baostock as bs
        except ImportError:
            print('[数据] 需要安装 baostock: pip install baostock')
            return pd.DataFrame()

        code = self.cfg.STOCK_CODE
        bs_code = f'{"sh" if code.startswith("6") else "sz"}.{code}'
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=self.cfg.DATA_DAYS)).strftime('%Y-%m-%d')

        print(f'[数据:baostock] 查询 {bs_code} {start}~{end} ...')

        try:
            lg = bs.login()
            if lg.error_code != '0':
                print(f'[数据:baostock] 登录失败: {lg.error_msg}')
                return pd.DataFrame()

            rs = bs.query_history_k_data_plus(
                bs_code,
                'date,open,high,low,close,preclose,volume,amount',
                start_date=start, end_date=end,
                frequency='d', adjustflag='3'  # 前复权
            )

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            bs.logout()

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount'])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df = df[(df['close'] > 0) & (df['volume'] > 0)]

            print(f'[数据:baostock] 获取 {len(df)} 条有效日线')
            return df

        except Exception as e:
            print(f'[数据:baostock] 失败: {e}')
            try:
                bs.logout()
            except Exception:
                pass
            return pd.DataFrame()

    def get_snapshot(self) -> Optional[dict]:
        """
        获取实时行情快照。

        返回: {lastPrice, open, high, low, volume, amount, bid1, ask1, ...}
        或 None (非交易时段/数据不可用)
        """
        code = self.cfg.stock_xt
        try:
            tick = xtdata.get_full_tick([code])
            if code in tick:
                return tick[code]
        except Exception as e:
            print(f'[行情] get_full_tick 异常: {e}')
        return None

    def subscribe_realtime(self):
        """订阅实时行情推送"""
        code = self.cfg.stock_xt
        xtdata.subscribe_quote(code, period='tick', start_time='', end_time='')

    def unsubscribe_realtime(self):
        """取消实时行情订阅"""
        code = self.cfg.stock_xt
        xtdata.unsubscribe_quote(code, period='tick', start_time='', end_time='')


# ============================================================================
# 交易模块 (xttrader)
# ============================================================================

class MyTraderCallback(xttrader.XtQuantTraderCallback):
    """
    交易回调 — MiniQMT 推送委托/成交/持仓变化时触发。

    所有回调都在 MiniQMT 的后台线程中执行,
    不要在此做耗时操作。
    """

    def __init__(self):
        super().__init__()
        self.orders: List[dict] = []    # 委托记录
        self.trades: List[dict] = []    # 成交记录
        self.positions: dict = {}        # 最新持仓
        self.account_info: dict = {}     # 账户信息

    def on_connected(self):
        """连接成功"""
        print('[交易] MiniQMT 交易服务已连接')

    def on_disconnected(self, reason):
        """连接断开"""
        print(f'[交易] 连接断开: {reason}')

    def on_stock_order(self, order):
        """
        委托回报 (核心回调).

        order 常用属性:
          m_strInstrumentID — 股票代码 (如 '601869')
          m_nOrderStatus    — 状态: 50=已报, 52=部成, 53=全成, 54=部撤, 55=已撤, 56=废单
          m_dLimitPrice     — 委托价格
          m_nVolumeTotalOriginal — 委托总数量
          m_nVolumeTraded   — 已成交数量
          m_nDirection      — 1=买, 2=卖
          m_nOrderNum       — 委托编号
        """
        status_map = {50: '已报', 52: '部成', 53: '全成', 54: '部撤', 55: '已撤', 56: '废单'}
        status = getattr(order, 'm_nOrderStatus', -1)
        status_text = status_map.get(status, f'未知({status})')

        direction = '买' if getattr(order, 'm_nDirection', 0) == 1 else '卖'
        price = getattr(order, 'm_dLimitPrice', 0)
        vol_total = getattr(order, 'm_nVolumeTotalOriginal', 0)
        vol_traded = getattr(order, 'm_nVolumeTraded', 0)
        code = getattr(order, 'm_strInstrumentID', '')

        ts = datetime.now().strftime('%H:%M:%S')
        msg = (f'[委托 {ts}] {direction} {code} '
               f'Y{price:.2f} x {vol_traded}/{vol_total}股 → {status_text}')

        if status in (54, 55, 56):  # 异常状态
            msg += ' ⚠'
        print(msg)

        self.orders.append({
            'time': ts, 'code': code, 'direction': direction,
            'price': price, 'volume': vol_total, 'filled': vol_traded,
            'status': status_text,
        })

    def on_stock_trade(self, trade):
        """
        成交回报.

        trade 常用属性:
          m_strInstrumentID — 股票代码
          m_nDirection      — 1=买, 2=卖
          m_dPrice          — 成交价
          m_nVolume         — 成交数量
        """
        direction = '买' if getattr(trade, 'm_nDirection', 0) == 1 else '卖'
        price = getattr(trade, 'm_dPrice', 0)
        volume = getattr(trade, 'm_nVolume', 0)
        code = getattr(trade, 'm_strInstrumentID', '')

        ts = datetime.now().strftime('%H:%M:%S')
        print(f'[成交 {ts}] {direction} {code} Y{price:.2f} x {volume}股 = Y{price*volume:,.0f}')

        self.trades.append({
            'time': ts, 'code': code, 'direction': direction,
            'price': price, 'volume': volume,
        })

    def on_stock_position(self, position):
        """持仓信息 (查询时回调) — position 是 XtPosition 对象"""
        self.positions[position.stock_code] = {
            'volume': position.volume,
            'cost': position.open_price,
            'last': position.last_price,
            'profit': position.float_profit,
        }

    def on_stock_asset(self, asset):
        """账户资产信息 — asset 是 XtAsset 对象"""
        self.account_info = {
            'available': asset.cash,
            'balance': asset.total_asset,
            'market_value': asset.market_value,
        }

    def on_order_error(self, order_error):
        """下单错误"""
        print(f'[下单错误] {order_error.error_msg if hasattr(order_error, "error_msg") else order_error}')

    def on_cancel_error(self, cancel_error):
        """撤单错误"""
        print(f'[撤单错误] {cancel_error.error_msg if hasattr(cancel_error, "error_msg") else cancel_error}')

    def on_account_status(self, account_status):
        """账号状态变化"""
        print(f'[账号状态] {account_status}')


class Trader:
    """
    封装 xttrader 交易操作。

    关键 API (已验证):
      XtQuantTrader(path, session, callback) — 构造函数
      connect()          → 0=成功
      query_account_infos() → [XtAccountInfo, ...]
      subscribe(account) → 0=成功
      query_stock_asset(account)      → XtAsset (同步)
      query_stock_positions(account)  → [XtPosition, ...] (同步)
      order_stock(account, code, order_type, volume, price_type, price, strategy, remark)
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.callback = MyTraderCallback()
        self._xt = xttrader.XtQuantTrader(
            cfg.MINIQMT_PATH, cfg.SESSION_ID, self.callback
        )
        self._connected = False
        self._account = None  # XtAccountInfo 对象

    def connect(self) -> bool:
        """连接 MiniQMT 交易服务"""
        try:
            self._xt.start()

            ret = self._xt.connect()
            if ret != 0:
                print(f'[交易] 连接失败, 返回码: {ret}')
                print()
                print(f'  常见原因排查:')
                print(f'  1. MiniQMT 是否启动?  (QMT → 右上角"极简模式")')
                print(f'  2. MiniQMT 中是否已登录资金账号?')
                print(f'  3. MINIQMT_PATH 是否正确?  ({self.cfg.MINIQMT_PATH})')
                print(f'     - 从 xtdata 连接日志可看到"数据路径"')
                return False

            # 获取账号列表
            accounts = self._xt.query_account_infos()
            if not accounts:
                print('[交易] 未找到任何账号 — 请在MiniQMT中登录资金账号')
                return False

            # 找到匹配的账号
            for acc in accounts:
                if acc.account_id == self.cfg.ACCOUNT_ID:
                    self._account = acc
                    break

            if self._account is None:
                # 如果没找到指定账号, 用第一个
                self._account = accounts[0]
                print(f'[交易] 未找到账号{self.cfg.ACCOUNT_ID}, 使用第一个: {self._account.account_id}')

            # 订阅账号 (让回调生效)
            sub_ret = self._xt.subscribe(self._account)
            print(f'[交易] 订阅账号 {self._account.account_id}, 返回: {sub_ret}')
            time.sleep(0.5)

            self._connected = True
            self._print_account_summary()
            return True

        except Exception as e:
            print(f'[交易] 连接异常: {e}')
            traceback.print_exc()
            return False

    def _print_account_summary(self):
        """打印账户摘要"""
        try:
            asset = self._xt.query_stock_asset(self._account)
            positions = self._xt.query_stock_positions(self._account)

            pos_str = '空仓'
            for pos in positions:
                if pos.volume > 0:
                    pos_str = f'{pos.stock_code} {pos.volume}股 (成本{pos.open_price:.2f})'

            print(f'[账户] 可用资金: {asset.cash:,.0f} | '
                  f'总资产: {asset.total_asset:,.0f} | '
                  f'市值: {asset.market_value:,.0f} | '
                  f'持仓: {pos_str}')
        except Exception as e:
            print(f'[账户] 查询异常: {e}')

    def disconnect(self):
        """断开连接"""
        if self._connected and self._account:
            self._xt.unsubscribe(self._account)
            self._xt.stop()
            self._connected = False

    def get_position(self) -> Optional[object]:
        """获取当前标的的持仓对象 (XtPosition)"""
        try:
            positions = self._xt.query_stock_positions(self._account)
            for pos in positions:
                if pos.stock_code == self.cfg.stock_xt and pos.volume > 0:
                    return pos
        except Exception:
            pass
        return None

    def get_cash(self) -> float:
        """获取可用资金"""
        try:
            asset = self._xt.query_stock_asset(self._account)
            return asset.cash
        except Exception:
            return 0.0

    def buy(self, price: float, lots: int = 1):
        """
        买入。

        order_stock(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)
          order_type:   xtconstant.STOCK_BUY (23) / STOCK_SELL (24)
          price_type:   xtconstant.FIX_PRICE (11) / LATEST_PRICE (5)
          order_volume: 数量(股)
        """
        shares = lots * self.cfg.TRADE_LOT

        if self.cfg.DRY_RUN:
            print(f'[模拟买入] {self.cfg.stock_xt} {price:.2f} x {shares}股')
            return True

        try:
            ret = self._xt.order_stock(
                self._account,              # XtAccountInfo 对象
                self.cfg.stock_xt,          # '601869.SH'
                xtconstant.STOCK_BUY,       # 买入
                shares,                     # 数量(股)
                xtconstant.FIX_PRICE,       # 限价
                price,                      # 价格
                'MA_CROSS',                 # 策略名
                '金叉买入',                   # 备注
            )
            print(f'[下单] 买入 {self.cfg.stock_xt} {price:.2f} x {shares}股, 委托号: {ret}')
            return True
        except Exception as e:
            print(f'[下单错误] 买入失败: {e}')
            return False

    def sell(self, price: float, lots: int = -1):
        """
        卖出。

        Args:
            price: 委托价格
            lots: 手数 (-1=全部持仓)
        """
        code = self.cfg.stock_xt

        if lots == -1:
            pos = self.get_position()
            shares = pos.volume if pos else 0
        else:
            shares = lots * self.cfg.TRADE_LOT

        if shares <= 0:
            print('[下单] 无可卖持仓')
            return False

        if self.cfg.DRY_RUN:
            print(f'[模拟卖出] {code} {price:.2f} x {shares}股')
            return True

        try:
            ret = self._xt.order_stock(
                self._account,
                code,
                xtconstant.STOCK_SELL,      # 卖出
                shares,
                xtconstant.FIX_PRICE,
                price,
                'MA_CROSS',
                '死叉卖出',
            )
            print(f'[下单] 卖出 {code} {price:.2f} x {shares}股, 委托号: {ret}')
            return True
        except Exception as e:
            print(f'[下单错误] 卖出失败: {e}')
            return False


# ============================================================================
# 策略核心逻辑
# ============================================================================

class DualMAStrategy:
    """双均线趋势跟踪策略"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = 'EMPTY'          # EMPTY | HOLDING
        self.position_cost = 0.0      # 持仓成本
        self.highest_price = 0.0      # 持仓期间最高价
        self.last_signal = ''         # 上一次信号
        self.fast_ma = 0.0
        self.slow_ma = 0.0
        self.trend_ma = 0.0
        self.prev_fast = 0.0
        self.prev_slow = 0.0
        self.total_trades = 0
        self.win_trades = 0
        self.total_pnl = 0.0

    def update(self, df: pd.DataFrame, snapshot: Optional[dict] = None):
        """
        计算均线并检测信号。

        Args:
            df: 历史日线数据 (最新行在最后)
            snapshot: 实时行情快照 (用于实盘价格)
        Returns:
            signal: 'BUY' | 'SELL' | 'HOLD' | ''
            price: 当前价格
        """
        if len(df) < self.cfg.SLOW_MA + 2:
            return '', 0.0

        closes = df['close'].values
        volumes = df['volume'].values

        # 计算均线 (SMA)
        self.fast_ma = np.mean(closes[-self.cfg.FAST_MA:])
        self.slow_ma = np.mean(closes[-self.cfg.SLOW_MA:])
        self.trend_ma = np.mean(closes[-self.cfg.TREND_MA:]) if len(closes) >= self.cfg.TREND_MA else 0

        # 前一根均线 (用于判断交叉)
        prev_closes = closes[:-1]
        self.prev_fast = np.mean(prev_closes[-self.cfg.FAST_MA:])
        self.prev_slow = np.mean(prev_closes[-self.cfg.SLOW_MA:])

        # 当前价格: 优先用实时快照, 否则用最新收盘价
        if snapshot and snapshot.get('lastPrice', 0) > 0:
            price = snapshot['lastPrice']
        else:
            price = closes[-1]

        # 交叉检测
        golden_cross = (self.prev_fast <= self.prev_slow) and (self.fast_ma > self.slow_ma)
        dead_cross = (self.prev_fast >= self.prev_slow) and (self.fast_ma < self.slow_ma)

        # 成交量过滤
        volume_ok = True
        if self.cfg.ENABLE_VOLUME_FILTER and len(volumes) >= 22:
            avg_vol = np.mean(volumes[-22:-1])
            cur_vol = volumes[-1]
            volume_ok = cur_vol > avg_vol * self.cfg.VOLUME_RATIO

        # 趋势过滤
        trend_ok = self.trend_ma <= 0 or price > self.trend_ma

        # ── 风控 (持仓时) ──
        if self.state == 'HOLDING' and self.position_cost > 0:
            # 更新最高价
            if price > self.highest_price:
                self.highest_price = price

            # 止损
            if price <= self.position_cost * (1 - self.cfg.STOP_LOSS_PCT):
                return 'SELL', price

            # 止盈
            if price >= self.position_cost * (1 + self.cfg.TAKE_PROFIT_PCT):
                return 'SELL', price

            # 回撤止盈
            if self.highest_price > self.position_cost * (1 + self.cfg.TAKE_PROFIT_PCT * 0.5):
                drawdown = (self.highest_price - price) / self.highest_price
                if drawdown >= self.cfg.TRAILING_STOP_PCT:
                    return 'SELL', price

        # ── 金叉买入 ──
        if golden_cross and volume_ok and trend_ok and self.state == 'EMPTY':
            self.last_signal = 'golden_cross'
            return 'BUY', price

        # ── 死叉卖出 ──
        if dead_cross and self.state == 'HOLDING':
            self.last_signal = 'dead_cross'
            return 'SELL', price

        return '', price

    def on_filled(self, action: str, price: float, shares: int):
        """成交后更新状态"""
        if action == 'BUY':
            self.state = 'HOLDING'
            self.position_cost = price
            self.highest_price = price
            self.total_trades += 1
        elif action == 'SELL':
            if self.position_cost > 0:
                pnl = (price - self.position_cost) * shares
                self.total_pnl += pnl
                if pnl > 0:
                    self.win_trades += 1
            self.state = 'EMPTY'
            self.position_cost = 0.0
            self.highest_price = 0.0


# ============================================================================
# 三种运行模式
# ============================================================================

def _is_trading_time() -> bool:
    """判断是否在A股交易时段"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.strftime('%H:%M')
    return ('09:30' <= t <= '11:30') or ('13:00' <= t <= '15:00')


def _log(msg: str):
    """统一日志"""
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')


def run_backtest(cfg: Config):
    """模式1: 纯本地回测 (不需要 MiniQMT)"""
    print(f'\n{"="*55}')
    print(f'  回测模式 — 双均线趋势跟踪')
    print(f'  标的: {cfg.STOCK_NAME}({cfg.STOCK_CODE})')
    print(f'  MA{cfg.FAST_MA} / MA{cfg.SLOW_MA} / MA{cfg.TREND_MA}')
    print(f'{"="*55}\n')

    # 从 baostock 获取历史数据 (纯本地, 无需 MiniQMT)
    print('[数据] 从 baostock 获取历史数据...')
    md = MarketData(cfg)
    # 回测模式不连 MiniQMT, 直接用 baostock
    md._connected = False
    df = md.download_history()

    if df.empty:
        print('[提示] 若首次使用, 需先连接MiniQMT下载一次数据')
        print('        启动 MiniQMT → python xtquant_dual_ma_strategy.py --mode signal')
        print('        (signal模式会自动下载数据)')
        return

    # 运行回测
    strategy = DualMAStrategy(cfg)
    trades = []
    equity = [cfg.MAX_LOTS * cfg.TRADE_LOT * 100]  # 假设初始资金

    for i in range(cfg.TREND_MA + 2, len(df)):
        sub_df = df.iloc[:i + 1]
        signal, price = strategy.update(sub_df)

        if signal == 'BUY':
            trades.append({'date': df.index[i] if hasattr(df, 'index') else i,
                          'action': 'BUY', 'price': price})
            strategy.on_filled('BUY', price, cfg.TRADE_LOT * cfg.MAX_LOTS)
            _log(f'[金叉买入] Y{price:.2f}')

        elif signal == 'SELL':
            trades.append({'date': df.index[i] if hasattr(df, 'index') else i,
                          'action': 'SELL', 'price': price})
            strategy.on_filled('SELL', price, cfg.TRADE_LOT * cfg.MAX_LOTS)
            pnl_str = f'Y{strategy.total_pnl:+,.0f}' if strategy.total_pnl != 0 else 'Y0'
            _log(f'[死叉卖出] Y{price:.2f} | 累计PnL {pnl_str}')

    # 回测报告
    sell_trades = [t for t in trades if t['action'] == 'SELL']
    win_cnt = sum(1 for t in sell_trades if any(
        ts['action'] == 'SELL' and ts.get('price', 0) > trades[idx - 1]['price']
        for idx, ts in enumerate(trades) if ts == t
    ))

    print(f'\n{"="*55}')
    print(f'  回测结果')
    print(f'  交易次数: {len(trades)}')
    print(f'  累计PnL:  Y{strategy.total_pnl:+,.0f}')
    print(f'  胜率:     {strategy.win_trades}/{max(1, strategy.total_trades)}')
    print(f'{"="*55}')


def run_signal(cfg: Config):
    """模式2: 实时信号模式 (连接 MiniQMT, 只发信号不下单)"""
    print(f'\n{"="*55}')
    print(f'  实时信号模式 — 只监控信号, 不下单')
    print(f'  标的: {cfg.STOCK_NAME}({cfg.STOCK_CODE})')
    print(f'{"="*55}\n')

    cfg.DRY_RUN = True  # 强制不下单

    # 连接行情
    md = MarketData(cfg)
    if not md.connect():
        return

    try:
        # 下载一次历史数据
        df = md.download_history()
        if df.empty:
            print('[错误] 无法获取历史数据')
            return

        strategy = DualMAStrategy(cfg)
        last_heartbeat = time.time()

        _log('开始监控... (Ctrl+C 停止)')

        while True:
            now = datetime.now()
            is_trading = _is_trading_time()

            # 非交易时段: 降低检查频率
            if not is_trading:
                time.sleep(60)
                if time.time() - last_heartbeat > 600:
                    _log(f'[心跳] 非交易时段, 策略待命中...')
                    last_heartbeat = time.time()
                continue

            # 获取实时行情
            snapshot = md.get_snapshot()
            price = snapshot.get('lastPrice', 0) if snapshot else 0

            # 更新策略
            signal, price = strategy.update(df, snapshot)

            # 心跳日志
            if time.time() - last_heartbeat > cfg.HEARTBEAT_MIN * 60:
                last_heartbeat = time.time()
                pos_str = f'持仓 Y{strategy.position_cost:.2f}' if strategy.state == 'HOLDING' else '空仓'
                _log(f'[心跳] Y{price:.2f} | {pos_str} | '
                     f'MA{cfg.FAST_MA}={strategy.fast_ma:.2f} '
                     f'MA{cfg.SLOW_MA}={strategy.slow_ma:.2f}')

            # 信号处理
            if signal == 'BUY':
                _log(f'[>>> 金叉买入信号] Y{price:.2f} '
                     f'MA{cfg.FAST_MA}={strategy.fast_ma:.2f} > MA{cfg.SLOW_MA}={strategy.slow_ma:.2f}')
                strategy.on_filled('BUY', price, cfg.TRADE_LOT * cfg.MAX_LOTS)
            elif signal == 'SELL':
                _log(f'[>>> 死叉卖出信号] Y{price:.2f} '
                     f'MA{cfg.FAST_MA}={strategy.fast_ma:.2f} < MA{cfg.SLOW_MA}={strategy.slow_ma:.2f}')
                strategy.on_filled('SELL', price, cfg.TRADE_LOT * cfg.MAX_LOTS)

            time.sleep(cfg.CHECK_INTERVAL_SEC)

    except KeyboardInterrupt:
        _log('\n用户中断')
    finally:
        md.disconnect()


def run_live(cfg: Config):
    """模式3: 实盘模式 (连接 MiniQMT, 自动下单)"""
    print(f'\n{"="*55}')
    print(f'  ⚠ 实盘模式 — 自动下单 ⚠')
    print(f'  标的: {cfg.STOCK_NAME}({cfg.STOCK_CODE})')
    print(f'  账号: {cfg.ACCOUNT_ID}')
    print(f'{"="*55}\n')

    cfg.DRY_RUN = False

    # 连接行情
    md = MarketData(cfg)
    if not md.connect():
        return

    # 连接交易
    trader = Trader(cfg)
    if not trader.connect():
        md.disconnect()
        return

    try:
        # 检查账户
        cash = trader.get_cash()
        pos = trader.get_position()  # XtPosition 对象或 None
        pos_vol = pos.volume if pos else 0
        pos_cost = pos.open_price if pos else 0
        _log(f'账户状态: 可用资金 {cash:,.0f}')
        _log(f'当前持仓: {pos_vol}股, 成本 {pos_cost:.2f}')

        # 下载历史数据
        df = md.download_history()
        if df.empty:
            _log('[错误] 无法获取历史数据')
            return

        strategy = DualMAStrategy(cfg)

        # 如果有持仓, 初始化策略状态
        if pos_vol > 0:
            strategy.state = 'HOLDING'
            strategy.position_cost = pos_cost
            strategy.highest_price = pos_cost
            _log(f'策略状态: HOLDING (从现有持仓恢复)')

        last_heartbeat = time.time()
        _log('\n实盘监控启动... (Ctrl+C 停止)')

        while True:
            if not _is_trading_time():
                time.sleep(60)
                if time.time() - last_heartbeat > 600:
                    _log(f'[心跳] 非交易时段, 策略待命...')
                    last_heartbeat = time.time()
                continue

            # 获取行情
            snapshot = md.get_snapshot()
            price = snapshot.get('lastPrice', 0) if snapshot else 0

            if price <= 0:
                time.sleep(cfg.CHECK_INTERVAL_SEC)
                continue

            # 策略信号
            signal, price = strategy.update(df, snapshot)

            if time.time() - last_heartbeat > cfg.HEARTBEAT_MIN * 60:
                last_heartbeat = time.time()
                pos = trader.get_position()
                cash = trader.get_cash()
                pos_vol = pos.volume if pos else 0
                _log(f'[心跳] {price:.2f} | 持仓{pos_vol}股 | 资金{cash:,.0f} | '
                     f'累计PnL {strategy.total_pnl:+,.0f}')

            # 执行交易
            if signal == 'BUY':
                cash = trader.get_cash()
                need = price * cfg.TRADE_LOT * cfg.MAX_LOTS * 1.001
                if cash >= need:
                    _log(f'[>>> 金叉买入] {price:.2f} x {cfg.MAX_LOTS}手')
                    trader.buy(price, cfg.MAX_LOTS)
                    strategy.on_filled('BUY', price, cfg.TRADE_LOT * cfg.MAX_LOTS)
                else:
                    _log(f'[金叉跳过] 资金不足: 需{need:,.0f} > {cash:,.0f}')

            elif signal == 'SELL':
                pos = trader.get_position()
                if pos and pos.volume > 0:
                    _log(f'[>>> 死叉卖出] {price:.2f} x {pos.volume}股')
                    trader.sell(price, -1)  # -1 = 全部卖出
                    strategy.on_filled('SELL', price, pos.volume)
                else:
                    _log(f'[死叉跳过] 无持仓')

            time.sleep(cfg.CHECK_INTERVAL_SEC)

    except KeyboardInterrupt:
        _log('\n用户中断')
    except Exception as e:
        _log(f'[异常] {e}')
        traceback.print_exc()
    finally:
        trader.disconnect()
        md.disconnect()
        _log(f'\n策略已停止 | 总交易{strategy.total_trades}次 | 累计PnL Y{strategy.total_pnl:+,.0f}')


# ============================================================================
# 主入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='MiniQMT + xtquant 双均线趋势跟踪策略',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
运行示例:
  python xtquant_dual_ma_strategy.py --mode backtest   (纯本地回测)
  python xtquant_dual_ma_strategy.py --mode signal     (实时信号, 不下单)
  python xtquant_dual_ma_strategy.py --mode live       (实盘自动交易)

前置条件:
  1. 启动 MiniQMT: QMT客户端 → 右上角"极简模式"
  2. pip install xtquant numpy pandas
        """
    )
    parser.add_argument('--mode', '-m', default='backtest',
                        choices=['backtest', 'signal', 'live'],
                        help='运行模式 (默认: backtest)')
    parser.add_argument('--dry-run', action='store_true',
                        help='实盘模式也只发信号不下单 (安全测试)')
    args = parser.parse_args()

    cfg = Config()
    if args.dry_run:
        cfg.DRY_RUN = True

    if args.mode == 'backtest':
        run_backtest(cfg)
    elif args.mode == 'signal':
        run_signal(cfg)
    elif args.mode == 'live':
        # 实盘前确认
        if not cfg.DRY_RUN:
            print('\n' + '!' * 55)
            print('  ⚠ 即将启动实盘自动交易!')
            print(f'  账号: {cfg.ACCOUNT_ID}')
            print(f'  标的: {cfg.STOCK_NAME}({cfg.STOCK_CODE})')
            print('  请确认 MiniQMT 已启动, 账户已登录')
            print('!' * 55)
            confirm = input('\n确认启动? (输入 yes 继续): ')
            if confirm.strip().lower() != 'yes':
                print('已取消')
                return
        run_live(cfg)


if __name__ == '__main__':
    main()
