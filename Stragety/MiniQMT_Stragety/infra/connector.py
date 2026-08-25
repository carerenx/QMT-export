# -*- coding: utf-8 -*-
"""
infra/connector.py — MiniQMT 连接层 + QMT 接口模拟
===================================================
  MiniQMTConnector  : 封装 xtdata + xttrader (行情/交易)
  MockContextInfo    : 模拟 QMT ContextInfo 对象
  MockPosition/Account : 模拟 QMT 持仓/账户对象
  get_trade_detail_data / order_shares : 模拟 QMT 全局函数
"""
import importlib
import os
import sys
import time as _time
import traceback as _traceback
from datetime import datetime, timedelta
from typing import Optional

from core import config as cfg
from .logger import _log

# ============================================================================
# 全局引用 (策略主循环中初始化)
# ============================================================================

_global_conn: Optional['MiniQMTConnector'] = None
_global_dry_run = False


def load_xtquant():
    """加载 XtQuant，必要时使用 MiniQMT 客户端随附的 Python SDK。"""
    try:
        return importlib.import_module('xtquant')
    except ModuleNotFoundError as first_error:
        sdk_path = os.path.abspath(cfg.XTQUANT_SITE_PACKAGES)
        package_path = os.path.join(sdk_path, 'xtquant')
        if not os.path.isdir(package_path):
            raise RuntimeError(
                '未找到 XtQuant SDK。当前检查路径: {}。请确认 MiniQMT '
                '安装位置，或设置 MINIQMT_PATH/XTQUANT_SITE_PACKAGES '
                '环境变量。'.format(sdk_path)
            ) from first_error
        if sdk_path not in sys.path:
            sys.path.insert(0, sdk_path)
        importlib.invalidate_caches()
        try:
            return importlib.import_module('xtquant')
        except (ImportError, OSError) as sdk_error:
            raise RuntimeError(
                'XtQuant SDK 存在但加载失败: {}。Python 必须是受支持的 '
                '64 位版本，并与客户端 SDK 兼容。'.format(sdk_error)
            ) from sdk_error


def set_global_conn(conn: 'MiniQMTConnector', dry_run: bool = False):
    """设置全局连接引用 (StrategyRunner.run() 中调用)"""
    global _global_conn, _global_dry_run
    _global_conn = conn
    _global_dry_run = dry_run


def _pick(obj, *names, default=None):
    """按顺序取第一个存在的字段值。

    兼容 xttrader 委托/成交对象的多种结构:
      - 官方 XtOrder/XtTrade: order_status / price / traded_volume / order_volume ...
      - 原生 C++ 对象: m_nOrderStatus / m_dPrice / m_nTradedVolume ...
      - dict / SON 对象: obj['字段'] 下标访问 (兜底)
    """
    for n in names:
        # 1) 属性访问 (pybind11 C++ 对象 / Python 类)
        try:
            v = getattr(obj, n)
        except Exception:
            v = None
        if v is not None:
            return v
        # 2) 下标访问 (dict / SON)
        try:
            v = obj[n]
        except Exception:
            continue
        if v is not None:
            return v
    return default


# ============================================================================
# MiniQMTConnector
# ============================================================================

class MiniQMTConnector:
    """封装 xtdata + xttrader 连接, 提供行情/交易查询/下单接口"""

    def __init__(self):
        self.xtdata = None
        self.xttrader = None
        self.trader = None
        self.callback = None
        self._data_connected = False
        self._trade_connected = False
        self._account_obj = None
        self._daily_data_cache = None
        self.last_position_query_ok = False
        self.last_account_query_ok = False

        self.last_order_status = None
        self.last_order_id = None
        self.last_order = None
        self.last_trade = None
        self.order_pending = False
        # ★ 下单快照 (方向, 价格, 数量, 代码) — 推送对象价格/数量字段为 0 时用于回退
        self.last_order_info = None
        self._order_field_dumped = False

    # ── 连接 / 断开 ──

    def connect_data(self):
        """连接行情 (xtdata)"""
        try:
            load_xtquant()
            _xtdata = importlib.import_module('xtquant.xtdata')
            self.xtdata = _xtdata
            # XtData 会在首次行情请求时自动连接 MiniQMT；官方 SDK 没有
            # xtdata.connect()。用无下单副作用的快照请求作为连接探针。
            _xtdata.get_full_tick([cfg.STOCK_QMT])
            self._data_connected = True
            _log('[连接] MiniQMT 行情服务已连接')
            return True
        except Exception as e:
            _log(f'[连接] 行情服务连接失败: {e}')
            _log('[提示] 请先启动 MiniQMT (QMT → 右上角"极简mode")')
            return False

    def connect_trade(self, account_id=cfg.ACCOUNT, path=cfg.MINIQMT_PATH, session=cfg.SESSION_ID):
        """连接交易 (xttrader)"""
        if not os.path.isdir(path):
            _log('[交易] MiniQMT 路径不存在: {}'.format(path))
            _log('[提示] 请设置 MINIQMT_PATH 为客户端 userdata_mini 完整路径')
            return False
        try:
            load_xtquant()
            _xttrader = importlib.import_module('xtquant.xttrader')
            xtconstant = importlib.import_module('xtquant.xtconstant')
        except RuntimeError as exc:
            _log('[交易] {}'.format(exc))
            return False

        class _Callback(_xttrader.XtQuantTraderCallback):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent

            def on_connected(self):
                _log('[交易] MiniQMT 交易服务已连接')

            def on_disconnected(self, reason):
                _log(f'[交易] 连接断开: {reason}')

            def on_stock_order(self, order):
                from xtquant import xtconstant
                sm = {
                    xtconstant.ORDER_UNREPORTED: '未报',
                    xtconstant.ORDER_WAIT_REPORTING: '待报',
                    xtconstant.ORDER_REPORTED: '已报',
                    xtconstant.ORDER_REPORTED_CANCEL: '已报待撤',
                    xtconstant.ORDER_PARTSUCC_CANCEL: '部成待撤',
                    xtconstant.ORDER_PART_CANCEL: '部撤',
                    xtconstant.ORDER_CANCELED: '已撤',
                    xtconstant.ORDER_PART_SUCC: '部成',
                    xtconstant.ORDER_SUCCEEDED: '全成',
                    xtconstant.ORDER_JUNK: '废单',
                }
                # ★ xttrader 委托对象字段 (XtOrder): order_status/price/traded_volume/order_volume
                #   _pick 兼容原生 m_ 前缀字段 (m_nOrderStatus/m_dPrice/m_nTradedVolume/m_nOrderVolume)
                status = _pick(order, 'order_status', 'm_nOrderStatus', default=-1)
                if status in sm:
                    # 买卖方向: 优先 order_type(23买/24卖), 回退 offset_flag(48开/49平)
                    otype = _pick(order, 'order_type', 'm_nOrderType', default=0)
                    offset_flag = _pick(order, 'offset_flag', 'm_nOffsetFlag', default=0)
                    if otype == xtconstant.STOCK_BUY:
                        d = '买'
                    elif otype == xtconstant.STOCK_SELL:
                        d = '卖'
                    elif offset_flag == xtconstant.OFFSET_FLAG_CLOSE:
                        d = '卖'
                    else:
                        d = '买'
                    p = _pick(order, 'price', 'm_dPrice', 'm_dLimitPrice', default=0.0)
                    vt = _pick(order, 'traded_volume', 'm_nTradedVolume', 'm_nVolumeTraded', default=0)
                    vo = _pick(order, 'order_volume', 'm_nOrderVolume', 'm_nVolumeTotalOriginal', default=0)
                    # ★ 一次性诊断: 推送对象价格/委托量字段为 0 时 dump 全部字段, 排查真实字段名/值
                    if (p <= 0 and vo <= 0) and not self.parent._order_field_dumped:
                        self.parent._order_field_dumped = True
                        _log('[委托诊断] 推送对象字段值:')
                        for attr in dir(order):
                            if not attr.startswith('_'):
                                try:
                                    _log(f'  {attr} = {getattr(order, attr)}')
                                except Exception:
                                    pass
                    # ★ 回退: 推送对象价格/委托量字段为 0 时, 用下单快照补齐真实价格/手数
                    sub = self.parent.last_order_info
                    if sub is not None:
                        _, _sub_price, _sub_shares, _ = sub
                        if p <= 0:
                            p = _sub_price
                        if vo <= 0:
                            vo = _sub_shares
                    _log(f'[委托] {d} Y{p:.2f} {vt}/{vo}股 -> {sm[status]}')
                    # 终态: 已撤/废单/全成 → 清除pending
                    if status in (xtconstant.ORDER_CANCELED, xtconstant.ORDER_JUNK,
                                  xtconstant.ORDER_SUCCEEDED):
                        self.parent.order_pending = False
                    # 废单时dump全部字段用于排错
                    if status == xtconstant.ORDER_JUNK:
                        _log('[废单诊断] 委托对象字段:')
                        for attr in dir(order):
                            if not attr.startswith('_'):
                                try:
                                    _log(f'  {attr} = {getattr(order, attr)}')
                                except Exception:
                                    pass
                self.parent.last_order = order
                self.parent.last_order_status = status
                oid = _pick(order, 'order_id', 'm_nOrderID', default=None)
                if oid is not None:
                    self.parent.last_order_id = oid

            def on_stock_trade(self, trade):
                from xtquant import xtconstant
                # 股票成交: 方向优先 order_type(23买/24卖), 回退 offset_flag(48开/49平)
                otype = _pick(trade, 'order_type', 'm_nOrderType', default=0)
                offset_flag = _pick(trade, 'offset_flag', 'm_nOffsetFlag', default=0)
                if otype == xtconstant.STOCK_BUY:
                    d = '买'
                elif otype == xtconstant.STOCK_SELL:
                    d = '卖'
                elif offset_flag == xtconstant.OFFSET_FLAG_CLOSE:
                    d = '卖'
                else:
                    d = '买'
                p = _pick(trade, 'traded_price', 'm_dTradedPrice', 'm_dPrice', default=0.0)
                v = _pick(trade, 'traded_volume', 'm_nTradedVolume', 'm_nVolume', default=0)
                code = _pick(trade, 'stock_code', 'm_strInstrumentID', default='')
                # ★ 回退: 推送对象价格/数量/代码字段为空时, 用下单快照补齐
                sub = self.parent.last_order_info
                if sub is not None:
                    _, _sub_price, _sub_shares, _sub_code = sub
                    if p <= 0:
                        p = _sub_price
                    if v <= 0:
                        v = _sub_shares
                    if not code:
                        code = _sub_code
                if not code:
                    code = cfg.STOCK_QMT
                _log(f'[成交] {d} {code} Y{p:.2f} x {v}股 = Y{p*v:,.0f}')
                self.parent.last_trade = trade
                self.parent.order_pending = False

            def on_stock_position(self, position):
                pass

            def on_stock_asset(self, asset):
                pass

            def on_order_error(self, order_error):
                msg = getattr(order_error, 'error_msg', str(order_error))
                _log(f'[下单错误] {msg}')
                self.parent.order_pending = False

            def on_cancel_error(self, cancel_error):
                _log(f'[撤单错误] {cancel_error}')

            def on_account_status(self, account_status):
                # 提取字段 (XtAccountStatus: account_id, account_type, status)
                acc_id = getattr(account_status, 'account_id', '?')
                acc_type = getattr(account_status, 'account_type', -1)
                acc_stat = getattr(account_status, 'status', -1)

                # 状态映射
                stat_map = {
                    xtconstant.ACCOUNT_STATUS_OK: '正常',
                    xtconstant.ACCOUNT_STATUS_WAITING_LOGIN: '等待登录',
                    xtconstant.ACCOUNT_STATUS_FAIL: '失败',
                    xtconstant.ACCOUNT_STATUS_INITING: '初始化中',
                    xtconstant.ACCOUNT_STATUS_CORRECTING: '校正中',
                    xtconstant.ACCOUNT_STATUS_CLOSED: '已关闭',
                    xtconstant.ACCOUNT_STATUS_ASSIS_FAIL: '辅助失败',
                    xtconstant.ACCOUNT_STATUS_DISABLEBYSYS: '系统禁用',
                    xtconstant.ACCOUNT_STATUS_DISABLEBYUSER: '用户禁用',
                }
                type_map = xtconstant.ACCOUNT_TYPE_DICT
                stat_name = stat_map.get(acc_stat, f'未知({acc_stat})')
                type_name = type_map.get(acc_type, f'未知({acc_type})')

                # 去重: 只有状态变化时才打印
                last = getattr(self.parent, '_last_acc_status', None)
                new_key = (acc_id, acc_type, acc_stat)
                if new_key != last:
                    self.parent._last_acc_status = new_key
                    _log(f'[账号状态] {acc_id} {type_name}: {stat_name}')
                # 非正常状态始终打印(不管是否重复)
                elif acc_stat != xtconstant.ACCOUNT_STATUS_OK:
                    _log(f'[账号状态] {acc_id} {type_name}: {stat_name} (重复)')

        self.callback = _Callback(self)
        self.xttrader = _xttrader
        self.trader = _xttrader.XtQuantTrader(path, session, self.callback)

        try:
            self.trader.start()
            ret = self.trader.connect()
            if ret != 0:
                _log(f'[交易] 连接失败, 返回码: {ret}')
                return False

            accounts = self.trader.query_account_infos()
            if not accounts:
                _log('[交易] 未找到资金账号 — 请在MiniQMT中登录账号')
                return False

            for acc in accounts:
                if acc.account_id == account_id:
                    self._account_obj = acc
                    break
            if self._account_obj is None:
                self._account_obj = accounts[0]
                _log(f'[交易] 使用账号: {self._account_obj.account_id}')

            self.trader.subscribe(self._account_obj)
            _time.sleep(0.5)
            self._trade_connected = True
            _log(f'[交易] 已订阅账号 {self._account_obj.account_id}')
            return True
        except Exception as e:
            _log(f'[交易] 连接异常: {e}')
            _traceback.print_exc()
            return False

    def disconnect(self):
        """断开所有连接"""
        if self._trade_connected and self._account_obj:
            try:
                self.trader.unsubscribe(self._account_obj)
                self.trader.stop()
            except Exception:
                pass
            self._trade_connected = False
        if self._data_connected:
            try:
                self.xtdata.disconnect()
            except Exception:
                pass
            self._data_connected = False

    @property
    def data_connected(self):
        return self._data_connected

    @property
    def trade_connected(self):
        return self._trade_connected

    # ── 数据查询 ──

    def get_intraday_bars(self, period='10m', count=200):
        """
        Fetch intraday bar data from xtdata.

        Args:
            period: bar period ('1m', '5m', '10m', '15m', '30m', '1h')
            count: number of bars to fetch

        Returns:
            dict with 'close', 'open', 'high', 'low' lists, or None
        """
        code = cfg.STOCK_QMT
        try:
            # Step 1: auto-download missing intraday bars from server
            self.xtdata.download_history_data(
                code, period=period, start_time='', end_time=''
            )

            # Step 2: read from local cache
            df = self.xtdata.get_market_data(
                field_list=['open', 'high', 'low', 'close', 'volume'],
                stock_list=[code],
                period=period,
                count=count,
                dividend_type='front',
            )
            if df is not None and len(df) > 0:
                # get_market_data returns DataFrame directly (single stock)
                if len(df) > count:
                    df = df.iloc[-count:]
                return {
                    'close': df['close'].values.tolist(),
                    'open': df['open'].values.tolist(),
                    'high': df['high'].values.tolist(),
                    'low': df['low'].values.tolist(),
                    'volume': df['volume'].values.tolist(),
                }
            return None
        except Exception as e:
            _log(f'[Intraday] fetch error: {e}')
            return None

    def refresh_daily_cache(self):
        """强制刷新日线数据缓存（跨日时调用）"""
        self._daily_data_cache = None

    def get_history_data(self, length, period, field):
        """对应 QMT: ContextInfo.get_history_data(N, '1d', field)"""
        code = cfg.STOCK_QMT
        if self._daily_data_cache is None:
            end = datetime.now().strftime('%Y%m%d')
            start = (datetime.now() - timedelta(days=365 * 6)).strftime('%Y%m%d')
            # ★ 先下载最新日线，确保本地数据包含最近交易日
            try:
                self.xtdata.download_history_data(
                    code, period='1d', start_time='', end_time=''
                )
            except Exception:
                pass
            data = self.xtdata.get_local_data(
                field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
                stock_list=[code],
                period='1d',
                start_time=start,
                end_time=end,
                dividend_type='front',
                data_dir='C:/QMT/datadir',
            )
            if code in data and len(data[code]) > 0:
                self._daily_data_cache = data[code]
            else:
                return {}

        df = self._daily_data_cache
        field_map = {
            'close': 'close', 'open': 'open', 'high': 'high',
            'low': 'low', 'volume': 'volume', 'amount': 'amount',
        }
        col = field_map.get(field, field)
        if col in df.columns:
            vals = df[col].values.tolist()
            if len(vals) > length:
                vals = vals[-length:]
            return {code: vals}
        return {}

    def get_daily_bars(self, length=100):
        """返回带日期索引的日线 DataFrame 副本。"""
        if self._daily_data_cache is None:
            self.get_history_data(length, '1d', 'close')
        if self._daily_data_cache is None:
            return None
        df = self._daily_data_cache
        if len(df) > length:
            df = df.iloc[-length:]
        return df.copy()

    def get_full_tick(self, codes):
        """对应 QMT: ContextInfo.get_full_tick([code])"""
        try:
            tick = self.xtdata.get_full_tick(codes)
            return tick if tick else {}
        except Exception:
            return {}

    # ── 交易查询 ──

    def query_positions(self):
        if not self._trade_connected:
            self.last_position_query_ok = False
            return []
        try:
            positions = self.trader.query_stock_positions(self._account_obj)
            self.last_position_query_ok = True
            return positions or []
        except Exception as e:
            self.last_position_query_ok = False
            _log(f'[持仓查询异常] {e}')
            return []

    def query_account(self):
        if not self._trade_connected:
            self.last_account_query_ok = False
            return None
        try:
            asset = self.trader.query_stock_asset(self._account_obj)
            self.last_account_query_ok = asset is not None
            return asset
        except Exception as e:
            self.last_account_query_ok = False
            _log(f'[资金查询异常] {e}')
            return None

    def query_order(self, order_id):
        """按订单号查询当日委托；查询异常时返回 None。"""
        if not self._trade_connected or self._account_obj is None or order_id is None:
            return None
        try:
            return self.trader.query_stock_order(self._account_obj, order_id)
        except Exception as e:
            _log(f'[委托查询异常] order_id={order_id}: {e}')
            return None

    def get_order_snapshot(self, order_id):
        """返回与策略解耦的委托快照，供严格成交确认使用。"""
        if order_id is None:
            return None
        order = self.query_order(order_id)
        if order is None and self.last_order_id == order_id:
            order = self.last_order
        if order is None:
            return None

        from xtquant import xtconstant
        status = _pick(order, 'order_status', 'm_nOrderStatus', default=-1)
        status_names = {
            xtconstant.ORDER_UNREPORTED: 'UNREPORTED',
            xtconstant.ORDER_WAIT_REPORTING: 'WAIT_REPORTING',
            xtconstant.ORDER_REPORTED: 'REPORTED',
            xtconstant.ORDER_REPORTED_CANCEL: 'REPORTED_CANCEL',
            xtconstant.ORDER_PARTSUCC_CANCEL: 'PARTSUCC_CANCEL',
            xtconstant.ORDER_PART_CANCEL: 'PART_CANCEL',
            xtconstant.ORDER_CANCELED: 'CANCELED',
            xtconstant.ORDER_PART_SUCC: 'PARTIAL',
            xtconstant.ORDER_SUCCEEDED: 'FILLED',
            xtconstant.ORDER_JUNK: 'REJECTED',
        }
        terminal_values = {
            xtconstant.ORDER_PART_CANCEL,
            xtconstant.ORDER_CANCELED,
            xtconstant.ORDER_SUCCEEDED,
            xtconstant.ORDER_JUNK,
        }
        volume = int(_pick(order, 'order_volume', 'm_nOrderVolume',
                           'm_nVolumeTotalOriginal', default=0) or 0)
        traded = int(_pick(order, 'traded_volume', 'm_nTradedVolume',
                           'm_nVolumeTraded', default=0) or 0)
        traded_price = float(_pick(order, 'traded_price', 'm_dTradedPrice',
                                   default=0.0) or 0.0)
        return {
            'order_id': order_id,
            'status_code': status,
            'status': status_names.get(status, f'UNKNOWN({status})'),
            'terminal': status in terminal_values,
            'rejected': status == xtconstant.ORDER_JUNK,
            'order_volume': volume,
            'traded_volume': traded,
            'traded_price': traded_price,
        }

    # ── 下单 ──

    def order_stock(self, stock_code, shares, style, price=None):
        """对应 QMT: order_shares(...)"""
        from xtquant import xtconstant

        if not self._trade_connected:
            _log('[下单跳过-未连接交易]')
            return

        if shares > 0:
            order_type = xtconstant.STOCK_BUY
            dir_name = '买入'
        else:
            order_type = xtconstant.STOCK_SELL
            shares = abs(shares)
            dir_name = '卖出'

        style = str(style or 'LATEST').upper()
        order_price = price if price and price > 0 else 0
        peer_price = getattr(xtconstant, 'MARKET_PEER_PRICE_FIRST',
                             xtconstant.LATEST_PRICE)
        market_sh = getattr(xtconstant, 'MARKET_SH_CONVERT_5_CANCEL', peer_price)
        market_sz = getattr(xtconstant, 'MARKET_SZ_CONVERT_5_CANCEL', peer_price)
        price_types = {
            'FIX': xtconstant.FIX_PRICE,
            'LATEST': xtconstant.LATEST_PRICE,
            'COMPETE': peer_price,
            'MARKET': market_sh if stock_code.endswith('.SH') else market_sz,
        }
        price_type = price_types.get(style, xtconstant.FIX_PRICE)
        if style != 'FIX':
            order_price = 0

        # ★ 记录下单快照, 供委托/成交推送回调在价格/数量字段为 0 时回退显示真实值
        display_price = price if price and price > 0 else order_price
        self.last_order_info = (dir_name, display_price, shares, stock_code)
        self.last_order_id = None
        self.last_order_status = None
        self.last_order = None

        _log('  >>> 下单{}: {} Y{:.2f} x {}股 {}'.format(
            dir_name, style, display_price, shares, stock_code))

        try:
            ret = self.trader.order_stock(
                self._account_obj, stock_code, order_type, shares,
                price_type, order_price,
                cfg.STRATEGY_NAME, '迷你反T_{}'.format(dir_name),
            )
            if isinstance(ret, int) and ret > 0:
                self.last_order_id = ret
                self.order_pending = True
            else:
                self.order_pending = False
                _log('[下单失败] 返回订单号: {}'.format(ret))
            return ret
        except Exception as e:
            _log('[下单异常] {}'.format(e))
            self.order_pending = False
            return None

    def cancel_order(self, order_id):
        """撤单 (按 order_id)。超时未成交时调用, 避免委托挂单残留。"""
        if not self._trade_connected or order_id is None:
            return False
        try:
            result = self.trader.cancel_order_stock(self._account_obj, order_id)
            if result == 0:
                _log(f'[撤单] order_id={order_id}')
                return True
            _log(f'[撤单失败] order_id={order_id}, 返回码={result}')
            return False
        except Exception as e:
            _log(f'[撤单异常] {e}')
            return False


# ============================================================================
# QMT ContextInfo 模拟
# ============================================================================

class MockContextInfo:
    """模拟 QMT 的 ContextInfo 对象, 提供策略需要的接口"""

    def __init__(self, connector: MiniQMTConnector):
        self.conn = connector
        self.st = {}
        self.accID = cfg.ACCOUNT
        self._barpos = 0

    @property
    def barpos(self):
        return self._barpos

    @barpos.setter
    def barpos(self, value):
        self._barpos = value

    def set_universe(self, stock_list):
        pass

    def set_account(self, acc_id):
        self.accID = acc_id

    def get_history_data(self, length, period, field, dividend_type=0, skip_paused=True):
        if period != '1d':
            _log(f'[警告] 仅支持1d周期, 收到: {period}')
            return {}
        return self.conn.get_history_data(length, period, field)

    def get_full_tick(self, codes):
        return self.conn.get_full_tick(codes)

    def is_last_bar(self):
        return True

    def run_time(self, func_name, interval, start_time='', market=''):
        pass

    def get_stock_name(self, code):
        return cfg.STOCK_NAME

    def get_open_date(self, code):
        return '20140701'


# ============================================================================
# QMT 数据结构模拟
# ============================================================================

class MockPosition:
    """模拟 QMT 的持仓对象"""
    def __init__(self, xt_pos=None):
        if xt_pos is not None:
            self.m_strInstrumentID = xt_pos.stock_code.split('.')[0] if hasattr(xt_pos, 'stock_code') else ''
            self.m_nVolume = getattr(xt_pos, 'volume', 0)
            self.m_dOpenPrice = getattr(xt_pos, 'open_price', 0.0)
            self.m_nCanUseVolume = getattr(xt_pos, 'can_use_volume', getattr(xt_pos, 'volume', 0))
        else:
            self.m_strInstrumentID = ''
            self.m_nVolume = 0
            self.m_dOpenPrice = 0.0
            self.m_nCanUseVolume = 0


class MockAccount:
    """模拟 QMT 的账户对象"""
    def __init__(self, asset=None):
        if asset is not None:
            self.m_dAvailable = getattr(asset, 'cash', 0.0)
            self.m_dBalance = getattr(asset, 'total_asset', 0.0)
        else:
            self.m_dAvailable = 0.0
            self.m_dBalance = 0.0


# ============================================================================
# QMT 全局函数模拟
# ============================================================================

def get_trade_detail_data(account_id, account_type, data_type):
    """模拟 QMT: get_trade_detail_data(account, 'STOCK', 'POSITION'/'ACCOUNT')"""
    conn = _global_conn
    if conn is None:
        return []

    if data_type.upper() == 'POSITION':
        xt_positions = conn.query_positions()
        return [MockPosition(xp) for xp in xt_positions]

    elif data_type.upper() == 'ACCOUNT':
        asset = conn.query_account()
        if asset is not None:
            return [MockAccount(asset)]
        return [MockAccount()]

    return []


def order_shares(stockcode, shares, style='LATEST', price=None,
                 ContextInfo=None, accId=None):
    """
    模拟 QMT: order_shares(code, shares, style, price, ContextInfo, accId)
    兼容 QMT 的多种参数重排调用方式。
    """
    conn = _global_conn
    if conn is None:
        _log('[order_shares] 未连接, 跳过')
        return

    _price = price
    if price is not None and not isinstance(price, (int, float)):
        # price 参数实际传的是 ContextInfo
        _price = None

    if _global_dry_run:
        direction = '买入' if shares > 0 else '卖出'
        px = _price if _price else '(对手价)'
        _log(f'[模拟] {direction} {stockcode} {px} x {abs(shares)}股')
        return 0

    return conn.order_stock(stockcode, shares, style, _price)
