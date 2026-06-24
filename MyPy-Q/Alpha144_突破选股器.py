#coding:utf-8
"""
Alpha#144 流动性冲击 + 5日突破选股器
=====================================
选股条件（两者同时满足）:
  1. sumif(|ret|/amount, ret<0, 20) — 下跌日流动性冲击因子
  2. 收盘价突破5日新高 (当日收盘 > 过去5个交易日最高价)

输出: Markdown 文件，按因子值降序排列
  排名 | 代码 | 名称 | 因子值 | 下跌天数 | 日均成交额(万) | 当日涨跌幅 | 换手率 | 市值

股票范围:
  - 沪市主板: 600xxx, 601xxx, 603xxx, 605xxx
  - 深市主板+中小板: 000xxx, 001xxx, 002xxx
  - 剔除: 创业板(300xxx)、科创板(688xxx)、北交所、ST

数据来源:
  - 股票列表+基本资料: MiniQMT xtdata (get_stock_list_in_sector + get_instrument_detail_list)
  - 行情OHLCV: 优先 backtest/cache/ 本地CSV（已有659只），缓存缺失的才尝试 xtdata

用法:
  python MyPy-Q/Alpha144_突破选股器.py
"""

import os
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# -- 路径 --
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CACHE_DIR = os.path.join(PROJECT_ROOT, 'backtest', 'cache')

# -- 参数 --
FACTOR_WINDOW = 20             # 因子计算窗口（交易日）
BREAKOUT_DAYS = 5              # 突破天数（5日新高）
MIN_DAILY_AMOUNT = 3e7         # 最低日均成交额（3000万，过滤僵尸股）


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def fmt_market_cap(value: float) -> str:
    """格式化市值"""
    if value >= 1e12:
        return f"{value/1e12:.2f}万亿"
    elif value >= 1e8:
        return f"{value/1e8:.0f}亿"
    elif value >= 1e4:
        return f"{value/1e4:.0f}万"
    return f"{value:.0f}元"


def is_main_board(code: str) -> bool:
    """判断是否为主板/中小板"""
    if not code:
        return False
    prefix = code[:6]
    try:
        num = int(prefix[:3])
    except ValueError:
        return False
    # 创业板: 300xxx~301xxx
    if 300 <= num <= 301:
        return False
    # 科创板: 688xxx, 689xxx
    if 688 <= num <= 689:
        return False
    # 北交所: 4xxxxx, 8xxxxx, 9xxxxx
    if num >= 400 and num <= 499:
        return False
    if num >= 800 and num <= 999:
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# 1. 获取股票列表 + 基本资料（通过 MiniQMT xtdata）
# ═══════════════════════════════════════════════════════════════

def get_stock_list_with_info() -> list:
    """
    从 MiniQMT 获取沪深主板股票列表，含名称、总股本、流通股本。

    返回: [{'code': '600004.SH', 'name': '白云机场',
            'total_share': 2.5e9, 'float_share': 2.0e9}, ...]
    """
    from xtquant import xtdata

    print("  获取沪深A股列表...")
    all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
    print(f"  沪深A股共 {len(all_stocks)} 只")

    # 过滤主板
    main_board = [c for c in all_stocks if is_main_board(c)]
    print(f"  过滤后主板: {len(main_board)} 只")

    # 批量获取股票详情
    print("  获取股票基本资料...")
    details = xtdata.get_instrument_detail_list(main_board)

    stocks = []
    for code in main_board:
        detail = details.get(code, {})
        if not detail:
            continue

        name = detail.get('InstrumentName', '')
        if not name:
            continue

        # 过滤 ST / *ST
        if 'ST' in name or '*ST' in name:
            continue

        total_share = float(detail.get('TotalVolume', 0))
        float_share = float(detail.get('FloatVolume', 0))

        stocks.append({
            'code': code,
            'name': name,
            'total_share': total_share,
            'float_share': float_share,
        })

    print(f"  有效股票（排除ST）: {len(stocks)} 只")
    return stocks


# ═══════════════════════════════════════════════════════════════
# 2. 批量读取行情数据（xtdata 为主，CSV 缓存兜底）
# ═══════════════════════════════════════════════════════════════

def load_all_ohlcv(codes: list) -> dict:
    """
    批量读取所有股票的日线行情数据。

    策略:
      1. 先从 xtdata 批量读取（QMT datadir 本地缓存，~4s/200只）
      2. 缺失的从 backtest/cache/ CSV 缓存补充

    返回: {code: DataFrame with [date, open, high, low, close, preclose, volume, amount]}
    """
    from xtquant import xtdata

    end_date = datetime.now().strftime('%Y%m%d')
    # 往回取足够长保证至少30个交易日
    start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')

    print(f"  日期范围: {start_date} ~ {end_date}")
    print(f"  目标 {len(codes)} 只股票")

    # ---- 第1步: xtdata 分批批量读取 ----
    all_data = {}
    CHUNK = 500

    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        chunk_idx = i // CHUNK + 1
        total_chunks = (len(codes) + CHUNK - 1) // CHUNK

        try:
            data = xtdata.get_market_data_ex(
                field_list=[],  # all fields: open, high, low, close, volume, amount, preClose...
                stock_list=chunk,
                period='1d',
                start_time=start_date,
                end_time=end_date,
                count=30,  # 最近30条日线
                dividend_type='front',
                fill_data=True
            )
            all_data.update(data)
            print(f"    xtdata chunk {chunk_idx}/{total_chunks}: "
                  f"返回 {len(data)}/{len(chunk)} 只")
        except Exception as e:
            print(f"    xtdata chunk {chunk_idx}/{total_chunks} 失败: {e}")

    print(f"  xtdata 共获取 {len(all_data)} 只")

    # ---- 第2步: 缺失的从 CSV 缓存补充 ----
    missing = [c for c in codes if c not in all_data]
    if missing:
        print(f"  缺失 {len(missing)} 只，从 CSV 缓存补充...")
        csv_count = 0
        for code in missing:
            df = _load_from_csv(code)
            if len(df) >= FACTOR_WINDOW + 1:
                all_data[code] = df
                csv_count += 1
        print(f"  CSV 补充 {csv_count}/{len(missing)} 只")

    # ---- 第3步: 统一处理格式 ----
    processed = {}
    for code, df in all_data.items():
        if df is None or df.empty:
            continue
        df = df.copy()

        # 时间列处理
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms') + pd.Timedelta(hours=8)
            df = df.sort_values('time').reset_index(drop=True)

        # 过滤停牌
        if 'volume' in df.columns:
            df = df[df['volume'] > 0]

        # 统一列名
        rename = {}
        if 'preClose' in df.columns and 'preclose' not in df.columns:
            rename['preClose'] = 'preclose'
        if 'time' in df.columns and 'date' not in df.columns:
            rename['time'] = 'date'
        if rename:
            df = df.rename(columns=rename)

        # 确保必要列存在
        for col in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount']:
            if col not in df.columns:
                df[col] = np.nan

        # 只保留最近30条有效数据
        df = df.tail(30).dropna(subset=['close', 'high', 'amount'])
        df = df[df['amount'] > 0]

        if len(df) < FACTOR_WINDOW + 1:
            continue

        processed[code] = df

    print(f"  最终有效行情: {len(processed)} 只")
    return processed


def _load_from_csv(code: str) -> pd.DataFrame:
    """从 backtest/cache/ CSV 读取单只股票数据"""
    cache_path = os.path.join(CACHE_DIR, code + '.csv')
    if not os.path.exists(cache_path):
        return pd.DataFrame()

    try:
        for enc in ['utf-8', 'gbk']:
            try:
                df = pd.read_csv(cache_path, encoding=enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            return pd.DataFrame()

        if df.empty:
            return pd.DataFrame()

        # 找日期列
        date_col = 'date'
        if date_col not in df.columns:
            for c in df.columns:
                if 'date' in c.lower() or 'time' in c.lower():
                    date_col = c
                    break
            else:
                date_col = df.columns[0]

        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        df = df.dropna(subset=[date_col]).sort_values(date_col)

        result = pd.DataFrame()
        result['date'] = df[date_col]
        for col in ['open', 'high', 'low', 'close', 'preclose', 'volume', 'amount']:
            if col in df.columns:
                result[col] = pd.to_numeric(df[col], errors='coerce')
            else:
                result[col] = np.nan

        result = result.tail(30).dropna(subset=['close', 'high', 'amount'])
        result = result[result['amount'] > 0]
        return result
    except Exception:
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════
# 3. 因子与条件计算
# ═══════════════════════════════════════════════════════════════

def calc_alpha144(df: pd.DataFrame) -> tuple:
    """
    计算 Alpha#144 因子: Σ(|ret_i| / amount_i) for ret_i < 0, 过去20天

    返回: (因子值, 下跌天数) 或 (None, 0)
    """
    if len(df) < FACTOR_WINDOW + 1:
        return None, 0

    closes = df['close'].values[-FACTOR_WINDOW - 1:]
    amounts = df['amount'].values[-FACTOR_WINDOW - 1:]

    alpha = 0.0
    neg_days = 0

    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev <= 0 or np.isnan(prev) or np.isnan(curr):
            continue

        ret = (curr - prev) / prev
        if ret < 0:
            amt = amounts[i]
            if amt > 0 and not np.isnan(amt):
                alpha += abs(ret) / amt
                neg_days += 1

    return (alpha, neg_days) if neg_days > 0 else (None, 0)


def check_breakout(df: pd.DataFrame) -> bool:
    """
    收盘价是否突破5日新高: 当日收盘 > 过去5日最高价（不含当日）
    """
    if len(df) < BREAKOUT_DAYS + 1:
        return False

    highs = df['high'].values
    close_today = df['close'].values[-1]
    prev_5_highs = highs[-BREAKOUT_DAYS - 1:-1]

    if np.isnan(close_today) or close_today <= 0:
        return False

    prev_max = np.nanmax(prev_5_highs)
    if np.isnan(prev_max):
        return False

    return close_today > prev_max


# ═══════════════════════════════════════════════════════════════
# 4. 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("  Alpha#144 流动性冲击 + 5日突破 — 选股器")
    print(f"  条件1: sumif(|ret|/amount, ret<0, 20)  流动性冲击因子")
    print(f"  条件2: 收盘价突破{BREAKOUT_DAYS}日新高")
    print("  范围: 沪深主板+中小板（剔除创业板/科创板/ST/BJ）")
    print("  数据: MiniQMT xtdata 行情 + 基本资料")
    print("=" * 80)
    print()

    # -- 1. 获取股票列表 --
    print("* 步骤1: 获取股票列表 + 基本资料 (MiniQMT)...")
    t0 = time.time()
    stocks = get_stock_list_with_info()
    if not stocks:
        print("[错误] 无法获取股票列表，退出。")
        return
    print(f"  OK 共 {len(stocks)} 只  (耗时 {time.time()-t0:.1f}s)")
    print()

    # -- 2. 批量获取行情数据 --
    print("* 步骤2: 批量获取行情数据 (xtdata)...")
    t0 = time.time()
    codes = [s['code'] for s in stocks]
    ohlcv_data = load_all_ohlcv(codes)
    print(f"  OK 有效行情: {len(ohlcv_data)} 只  (耗时 {time.time()-t0:.1f}s)")
    print()

    # -- 3. 逐只计算因子 + 检查突破 --
    print("* 步骤3: 计算因子 + 检查突破...")

    breakout_results = []   # 满足两条件
    all_valid = []          # 因子计算成功的
    skipped = 0
    n_stocks = len(stocks)

    t0 = time.time()
    for i, stock in enumerate(stocks):
        if (i + 1) % 200 == 0 or i == 0 or i == n_stocks - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 0.1)
            print(f"    进度: {i+1}/{n_stocks} ({100*(i+1)/n_stocks:.0f}%) "
                  f"| 有效: {len(all_valid)} | 突破: {len(breakout_results)} "
                  f"| 跳过: {skipped} | {rate:.0f}只/s")

        code = stock['code']

        # 数据是否存在
        if code not in ohlcv_data:
            skipped += 1
            continue

        df = ohlcv_data[code]

        # 成交额过滤
        avg_amount = df['amount'].tail(FACTOR_WINDOW).mean()
        if avg_amount < MIN_DAILY_AMOUNT:
            skipped += 1
            continue

        # 计算因子
        alpha, neg_days = calc_alpha144(df)
        if alpha is None:
            skipped += 1
            continue

        # 检查突破
        is_breakout = check_breakout(df)

        # 基础信息
        latest_row = df.iloc[-1]
        latest_close = latest_row['close']
        total_share = stock['total_share']
        float_share = stock['float_share']

        market_cap = latest_close * total_share if total_share > 0 else 0

        all_valid.append({
            'code': code,
            'name': stock['name'],
            'alpha': alpha,
            'neg_days': neg_days,
            'avg_amount': avg_amount,
            'breakout': is_breakout,
            'market_cap': market_cap,
        })

        if is_breakout:
            preclose_val = latest_row.get('preclose', np.nan)
            latest_volume = latest_row['volume']

            if not np.isnan(preclose_val) and preclose_val > 0:
                pct_chg = (latest_close - preclose_val) / preclose_val * 100
            else:
                pct_chg = 0.0

            if float_share > 0 and latest_volume > 0:
                turnover = latest_volume / float_share * 100
            else:
                turnover = 0.0

            breakout_results.append({
                'code': code,
                'name': stock['name'],
                'alpha': alpha,
                'neg_days': neg_days,
                'avg_amount': avg_amount,
                'pct_chg': pct_chg,
                'turnover': turnover,
                'market_cap': market_cap,
            })

    n_valid = len(all_valid)
    n_breakout = len(breakout_results)
    elapsed = time.time() - t0
    print(f"\n  OK! 计算耗时 {elapsed:.1f}s")
    print(f"    有效股票: {n_valid}  |  突破5日新高: {n_breakout}  |  跳过: {skipped}")
    print()

    if n_valid == 0:
        print("[提示] 无有效股票。")
        _write_markdown([], [], [])
        return

    # -- 4. 排序 --
    breakout_results.sort(key=lambda x: x['alpha'], reverse=True)
    all_valid.sort(key=lambda x: x['alpha'], reverse=True)
    breakout_pool = [r for r in all_valid if r['breakout']]
    non_breakout_pool = [r for r in all_valid if not r['breakout']]

    alphas_all = np.array([r['alpha'] for r in all_valid])
    print(f"* 排名统计:")
    print(f"  +----------------------┬--------------+")
    print(f"  | 有效股票数             | {n_valid:<13} |")
    print(f"  | 突破股票数             | {n_breakout:<13} |")
    print(f"  | 突破比例               | {n_breakout/max(n_valid,1)*100:<13.1f}% |")
    print(f"  | 因子均值               | {np.mean(alphas_all):<13.2e} |")
    print(f"  | 因子中位数             | {np.median(alphas_all):<13.2e} |")
    print(f"  +----------------------┴--------------+")
    print()

    # -- 5. 输出 Markdown --
    md_path = _write_markdown(breakout_results, breakout_pool, non_breakout_pool)
    print(f"  Markdown 报告: {md_path}")

    # -- 6. 控制台打印前30 --
    if breakout_results:
        print()
        print("=" * 80)
        print("  【突破选股结果 — 前30名】")
        print("=" * 80)
        fmt_header = (f"  {'排名':<4} {'代码':<12} {'名称':<9} "
                      f"{'因子值':<14} {'下跌日':<6} {'日均成交(万)':<12} "
                      f"{'涨跌幅':<8} {'换手率':<8} {'市值':<10}")
        print(fmt_header)
        print(f"  {'-'*92}")

        for rank, r in enumerate(breakout_results[:30], 1):
            name = r['name'][:4] if len(r['name']) > 4 else r['name']
            print(f"  {rank:<4} {r['code']:<12} {name:<9} "
                  f"{r['alpha']:<14.4e} {r['neg_days']:<6} "
                  f"{r['avg_amount']/1e4:<12.0f} "
                  f"{r['pct_chg']:>+6.2f}%  {r['turnover']:<7.2f}% "
                  f"{fmt_market_cap(r['market_cap']):<10}")
    else:
        print()
        print("[提示] 无股票同时满足两条件，请查看Markdown报告中的因子排名。")

    print()
    print("=" * 80)
    print(f"  筛选日期:  {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  数据范围:  过去 {FACTOR_WINDOW} 个交易日")
    print(f"  突破条件:  收盘 > 过去{BREAKOUT_DAYS}日最高价")
    print(f"  最低日均额: {MIN_DAILY_AMOUNT/1e4:.0f} 万")
    print(f"  数据来源:  MiniQMT xtdata 行情 + 基本资料")
    print(f"  股票池:    沪深主板+中小板（突破 {n_breakout} / 有效 {n_valid}）")
    print("=" * 80)


# ═══════════════════════════════════════════════════════════════
# 5. Markdown 输出
# ═══════════════════════════════════════════════════════════════

def _write_markdown(results: list, breakout_pool: list, non_breakout_pool: list) -> str:
    """输出 Markdown 报告"""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    timestamp = now.strftime('%Y%m%d_%H%M')
    md_path = os.path.join(SCRIPT_DIR, f'Alpha144_突破选股_{timestamp}.md')

    n_total = len(breakout_pool) + len(non_breakout_pool)

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# Alpha#144 流动性冲击 + 5日突破 — 选股结果\n\n")
        f.write(f"> **筛选日期**: {date_str}\n")
        f.write(f"> **公式**: sumif(|ret|/amount, ret<0, {FACTOR_WINDOW})\n")
        f.write(f"> **突破条件**: 收盘 > 过去{BREAKOUT_DAYS}日最高价\n")
        f.write(f"> **股票池**: 沪深主板+中小板（剔除创业板/科创板/ST/BJ）\n")
        f.write(f"> **最低日均成交额**: {MIN_DAILY_AMOUNT/1e4:.0f} 万\n")
        f.write(f"> **入选数**: {len(results)} / 有效 {n_total}\n\n")
        f.write("---\n\n")

        # -- 表1: 满足两条件 --
        f.write(f"## 一、突破选股结果（满足两条件，共 {len(results)} 只）\n\n")

        if results:
            f.write("| 排名 | 代码 | 名称 | 因子值 | 下跌天数 | 日均成交额(万) | "
                    "当日涨跌幅 | 换手率 | 市值 |\n")
            f.write("|------|------|------|--------|----------|----------------|"
                    "------------|--------|------|\n")
            for rank, r in enumerate(results, 1):
                pct_str = f"{r['pct_chg']:+.2f}%"
                turn_str = f"{r['turnover']:.2f}%" if r['turnover'] > 0 else "N/A"
                cap_str = fmt_market_cap(r['market_cap']) if r['market_cap'] > 0 else "N/A"
                f.write(f"| {rank} | {r['code']} | {r['name']} | "
                        f"{r['alpha']:.4e} | {r['neg_days']} | "
                        f"{r['avg_amount']/1e4:.0f} | "
                        f"{pct_str} | {turn_str} | {cap_str} |\n")
            f.write("\n")
        else:
            f.write("> 当日无股票同时满足因子和突破条件。\n\n")

        # -- 表2: 因子排名 Top 50 --
        f.write(f"---\n\n")
        f.write(f"## 二、因子排名 Top 50（含突破标记）\n\n")

        all_pool = breakout_pool + non_breakout_pool
        all_pool.sort(key=lambda x: x['alpha'], reverse=True)
        top50 = all_pool[:50]

        if top50:
            f.write("| 排名 | 代码 | 名称 | 因子值 | 下跌天数 | "
                    "日均成交额(万) | 市值 | 突破5日新高 |\n")
            f.write("|------|------|------|--------|----------|"
                    "----------------|------|-------------|\n")
            for rank, r in enumerate(top50, 1):
                flag = "✅ **是**" if r['breakout'] else "❌"
                cap_str = fmt_market_cap(r['market_cap']) if r['market_cap'] > 0 else "N/A"
                f.write(f"| {rank} | {r['code']} | {r['name']} | "
                        f"{r['alpha']:.4e} | {r['neg_days']} | "
                        f"{r['avg_amount']/1e4:.0f} | "
                        f"{cap_str} | {flag} |\n")
            f.write("\n")

        # -- 表3: 统计 --
        f.write(f"---\n\n")
        f.write(f"## 三、统计信息\n\n")

        if breakout_pool:
            ab = np.array([r['alpha'] for r in breakout_pool])
            f.write(f"### 突破组（{len(breakout_pool)} 只）\n\n")
            f.write(f"| 指标 | 值 |\n|------|----|\n")
            f.write(f"| 因子均值 | {np.mean(ab):.4e} |\n")
            f.write(f"| 因子中位数 | {np.median(ab):.4e} |\n")
            f.write(f"| 因子标准差 | {np.std(ab):.4e} |\n")
            f.write(f"| 因子最大值 | {np.max(ab):.4e} |\n")
            f.write(f"| 因子最小值 | {np.min(ab):.4e} |\n\n")

        aa = np.array([r['alpha'] for r in all_pool])
        f.write(f"### 全市场（{len(all_pool)} 只有效股票）\n\n")
        f.write(f"| 指标 | 值 |\n|------|----|\n")
        f.write(f"| 因子均值 | {np.mean(aa):.4e} |\n")
        f.write(f"| 因子中位数 | {np.median(aa):.4e} |\n")
        f.write(f"| 因子标准差 | {np.std(aa):.4e} |\n")
        f.write(f"| 突破比例 | {len(breakout_pool)}/{n_total} = "
                f"{len(breakout_pool)/max(n_total,1)*100:.1f}% |\n\n")

        f.write(f"---\n\n")
        f.write(f"*报告由 Alpha144_突破选股器.py 于 {date_str} 自动生成*\n")

    return md_path


if __name__ == '__main__':
    main()
