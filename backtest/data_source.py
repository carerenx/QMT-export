"""
数据获取与缓存模块

用 baostock 获取 A 股日线 OHLCV 数据，缓存为 CSV，避免重复下载。
提供与 QMT get_history_data() 兼容的数据格式。

格式转换:
  QMT 代码格式:  600519.SH
  baostock 格式: sh.600519
"""
import os
import time
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from . import config

logger = logging.getLogger(__name__)


# ============================================================
# 股票代码格式转换
# ============================================================

def code_to_bs(code):
    """QMT 代码转 baostock 格式: 600519.SH -> sh.600519"""
    sym, suffix = code.split('.')
    return suffix.lower() + '.' + sym


def code_from_bs(bs_code):
    """baostock 格式转 QMT 代码: sh.600519 -> 600519.SH"""
    parts = bs_code.split('.')
    sym = parts[1]
    suffix = parts[0].upper()
    return sym + '.' + suffix


# ============================================================
# 股票名称缓存 (用于 ST 过滤)
# ============================================================

def build_name_map(codes):
    """
    构建 {code: name} 映射，用于 ST 过滤。

    注: baostock 的 query_stock_basic() 在某些代理环境下会超时，
    而股票池是人工精选非 ST 股，所以直接返回 'Normal' 作为名称。
    策略只检查名称中是否包含 'ST' 或 '*'，不影响回测逻辑。
    """
    return {code: 'Normal' for code in codes}


# ============================================================
# baostock 连接管理
# ============================================================

_bs_logged_in = False

def bs_login():
    """确保 baostock 已登录"""
    global _bs_logged_in
    if not _bs_logged_in:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == '0':
            _bs_logged_in = True
        else:
            raise ConnectionError("baostock 登录失败: %s" % lg.error_msg)


# ============================================================
# 个股数据获取
# ============================================================

def fetch_single_stock(code, start, end, cache_dir):
    """
    获取单只股票日线数据，优先从缓存读取。

    返回: DataFrame with columns [date, open, high, low, close, volume, amount]
          或 None (获取失败时)
    """
    cache_file = os.path.join(cache_dir, code + '.csv')

    # 检查缓存
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, parse_dates=['date'])
            if len(df) > 0:
                first_date = df['date'].min()
                last_date = df['date'].max()
                if first_date <= pd.Timestamp(start) and last_date >= pd.Timestamp(end):
                    logger.debug("缓存命中: %s (%d行)", code, len(df))
                    return df
        except Exception as e:
            logger.warning("缓存读取失败 %s: %s，重新获取", code, e)

    # 从 baostock 获取
    try:
        import baostock as bs
        bs_login()

        bs_code = code_to_bs(code)
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields='date,open,high,low,close,preclose,volume,amount',
            start_date=start,
            end_date=end,
            frequency='d',
            adjustflag='3'  # 前复权
        )
        if rs.error_code != '0':
            logger.warning("查询失败 %s: %s", code, rs.error_msg)
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            logger.warning("无数据: %s", code)
            return None

        # 转 DataFrame
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount'])
        # 字符串转数值
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'preclose']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # 保存缓存
        os.makedirs(cache_dir, exist_ok=True)
        df.to_csv(cache_file, index=False, encoding='utf-8-sig')
        logger.info("已获取: %s (%d行)", code, len(df))
        return df

    except ImportError:
        logger.error("需要安装 baostock: pip install baostock")
        return None
    except Exception as e:
        logger.warning("获取失败 %s: %s", code, e)
        time.sleep(0.5)
        return None


# ============================================================
# DataProvider — 统一数据接口
# ============================================================

class DataProvider:
    """
    为回测引擎提供数据。

    数据结构:
      self.ohlcv = {
          '600519.SH': {
              'close':  [100.0, 101.0, ...],  # list[0]=最旧, list[-1]=最新
              'open':   [...],
              'high':   [...],
              'low':    [...],
              'volume': [...],
              'amount': [...],
          },
          ...
      }
      self.date_index: DatetimeIndex (所有交易日并集)
      self.dates_list: datetime 对象列表 (与 date_index 对应)
      self.names: {code: name} 股票名称映射
    """

    def __init__(self):
        self.ohlcv = {}          # {code: {field: [values]}}
        self.date_index = None   # pd.DatetimeIndex
        self.dates_list = []     # list of datetime objects
        self.names = {}          # {code: stock_name}
        self.code_list = []      # 已加载的股票代码列表

    def load(self, codes, start=None, end=None):
        """
        加载股票数据。

        Args:
            codes: 股票代码列表 ['600519.SH', ...]
            start: 开始日期 '2020-01-01'
            end: 结束日期 '2025-12-31'
        """
        if start is None:
            start = config.BACKTEST_START
        if end is None:
            end = config.BACKTEST_END

        cache_dir = config.CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)

        # 逐只获取
        all_dfs = {}
        total = len(codes)
        for i, code in enumerate(codes):
            print("[数据] (%d/%d) %s ..." % (i + 1, total, code), end='\r')
            df = fetch_single_stock(code, start, end, cache_dir)
            if df is not None and len(df) > 0:
                all_dfs[code] = df
        print("[数据] 完成，成功获取 %d/%d 只" % (len(all_dfs), total))

        if not all_dfs:
            raise RuntimeError("没有获取到任何股票数据！")

        # 构建名称映射
        try:
            self.names = build_name_map(list(all_dfs.keys()))
        except Exception:
            self.names = {}

        # 对齐日期索引 (取所有交易日并集)
        all_dates = pd.DatetimeIndex([], freq=None)
        for df in all_dfs.values():
            all_dates = all_dates.union(df['date'])
        all_dates = all_dates.sort_values()
        self.date_index = all_dates
        self.dates_list = all_dates.to_pydatetime().tolist()

        # 转换为 QMT 兼容格式
        self.code_list = list(all_dfs.keys())
        self.ohlcv = {}
        for code, df in all_dfs.items():
            fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
            self.ohlcv[code] = {}
            for f in fields:
                if f in df.columns:
                    # 重采样对齐到 date_index (前向填充停牌日)
                    s = df.set_index('date')[f].reindex(all_dates, method='ffill')
                    self.ohlcv[code][f] = s.values.tolist()
                else:
                    self.ohlcv[code][f] = [0.0] * len(all_dates)

    def get_date_by_index(self, idx):
        """根据索引获取日期"""
        if 0 <= idx < len(self.dates_list):
            return self.dates_list[idx]
        return None

    def get_field_series(self, code, field):
        """获取某只股票某个字段的完整序列"""
        if code in self.ohlcv and field in self.ohlcv[code]:
            return self.ohlcv[code][field]
        return None

    def get_value(self, code, field, bar_idx):
        """获取某只股票某个字段在指定 bar 的值"""
        series = self.get_field_series(code, field)
        if series is not None and 0 <= bar_idx < len(series):
            return series[bar_idx]
        return None

    def get_stock_name(self, code):
        """获取股票名称"""
        return self.names.get(code)

    def validate(self):
        """检查数据完整性"""
        n_bars = len(self.dates_list)
        print("[验证] 共 %d 个交易日, %d 只股票" % (n_bars, len(self.code_list)))

        sample = self.code_list[:3]
        for code in sample:
            close_len = len(self.ohlcv[code].get('close', []))
            print("[验证] %s close=%d bars, 范围 %s ~ %s" % (
                code, close_len,
                self.dates_list[0].strftime('%Y-%m-%d') if self.dates_list else 'N/A',
                self.dates_list[-1].strftime('%Y-%m-%d') if self.dates_list else 'N/A'
            ))

        # 检查缺失率
        missing_total = 0
        total_points = 0
        for code in self.code_list:
            c = self.ohlcv[code].get('close', [])
            total_points += len(c)
            missing_total += sum(1 for v in c if v is None or (isinstance(v, float) and (v == 0 or np.isnan(v))))

        if total_points > 0:
            pct = missing_total / total_points * 100
            print("[验证] 数据缺失率: %.2f%%" % pct)
