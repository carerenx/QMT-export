#coding:utf-8
"""
Alpha#144 流动性冲击 — 全市场股票筛选器（不含创业板/科创板）
============================================================
根据流动性冲击因子对沪深主板股票进行排名，
输出前 20%（最具反弹潜力）和后 20%（流动性最充沛）的股票。

【因子公式】
  alpha_144 = Σ(|ret_i| / amount_i)  对所有 ret_i < 0 的过去 20 个交易日

【因子解读】
  值越大 → 下跌日每元成交额引发的价格冲击越大
         → 恐慌抛售越极端 → 接盘资金稀缺 → 超跌反弹潜力大
  值越小 → 下跌时流动性充裕，有资金承接
         → 筹码结构健康，但短期缺乏超跌反弹机会

【股票范围】
  - 沪市主板: 600xxx, 601xxx, 603xxx, 605xxx
  - 深市主板: 000xxx, 001xxx
  - 深市中小板: 002xxx
  - 剔除: 创业板(300xxx)、科创板(688xxx)、北交所、ST、退市

【数据来源】
  - 优先使用 backtest/cache/ 的本地缓存CSV（659只，fast）
  - 缓存中找不到的从 baostock 下载
  - 股票列表也带缓存，避免频繁请求 baostock

用法:
  python MyPy-Q/Alpha144_流动性筛选器.py
"""

import os
import sys
import json
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
MIN_DAILY_AMOUNT = 3e7         # 最低日均成交额（3000万，过滤僵尸股）
TOP_BOTTOM_PCT = 0.20          # 输出前/后百分比
STOCK_LIST_CACHE = os.path.join(SCRIPT_DIR, 'a_share_stocks_cache.json')

# -- 已知 A 股代码范围（用于在线拉取失败时的兜底）--
# 只保留主板 + 中小板，剔除创业板/科创板/北交所
KNOWN_STOCK_PREFIXES = [
    ('sh.', 600, 609),   # 沪市主板: 600000 ~ 609999
    ('sh.', 601, 601),   # 沪市主板: 601000 ~ 601999
    ('sh.', 603, 603),   # 沪市主板: 603000 ~ 603999
    ('sh.', 605, 605),   # 沪市主板: 605000 ~ 605999
    ('sz.', 0, 2),       # 深市主板(000) + 中小板(001+002): 000000 ~ 002999
]


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def code_convert(bs_code: str) -> str:
    """baostock → QMT: sh.600004 → 600004.SH"""
    if bs_code.startswith('sh.'):
        return bs_code[3:] + '.SH'
    elif bs_code.startswith('sz.'):
        return bs_code[3:] + '.SZ'
    return bs_code


def reverse_code(qmt_code: str) -> str:
    """QMT → baostock: 600004.SH → sh.600004"""
    if qmt_code.endswith('.SH'):
        return 'sh.' + qmt_code[:-3]
    elif qmt_code.endswith('.SZ'):
        return 'sz.' + qmt_code[:-3]
    return qmt_code


# ═══════════════════════════════════════════════════════════════
# 获取符合条件的股票列表
# ═══════════════════════════════════════════════════════════════

def get_stock_list() -> list:
    """
    获取符合条件的沪深主板股票列表。

    优先级:
      1. 本地 JSON 缓存
      2. baostock query_all_stock 在线拉取
      3. 已知代码范围生成（兜底）

    返回: [{'bs_code': 'sh.600004', 'qmt_code': '600004.SH', 'name': '白云机场'}, ...]
    """
    # ═══ 1. 尝试从本地 JSON 缓存加载 ═══
    if os.path.exists(STOCK_LIST_CACHE):
        try:
            with open(STOCK_LIST_CACHE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'stocks' in data and len(data['stocks']) > 100:
                print(f"  从本地缓存加载 {len(data['stocks'])} 只股票")
                print(f"  （缓存时间: {data.get('update_time', '未知')}）")
                return data['stocks']
        except Exception:
            pass

    # ═══ 2. 尝试 baostock 在线拉取 ═══
    print("  本地缓存不存在，尝试在线获取...")
    import baostock as bs

    # 多试几次（baostock 有时会不稳定）
    for attempt in range(3):
        try:
            lg = bs.login()
            time.sleep(0.5)

            rs = bs.query_all_stock(datetime.now().strftime('%Y-%m-%d'))
            all_sec = []
            while (rs.error_code == '0') & rs.next():
                all_sec.append(rs.get_row_data())
            bs.logout()

            if len(all_sec) > 500:
                break  # 获取成功
            time.sleep(2)
        except Exception:
            time.sleep(2)
            continue

    # 过滤
    if len(all_sec) > 500:
        eligible = []
        for row in all_sec:
            code, status, name = row
            if status != '1':
                continue
            if 'ST' in name or '*' in name:
                continue

            # 沪市主板（600/601/603/605），避开科创板（688）
            if code.startswith('sh.60') and not code.startswith('sh.68'):
                eligible.append({
                    'bs_code': code,
                    'qmt_code': code_convert(code),
                    'name': name,
                })
            # 深市主板+中小板（000/001/002），避开创业板（300）
            elif code.startswith('sz.00'):
                sub = code[3:6]
                if sub in ('000', '001', '002'):
                    eligible.append({
                        'bs_code': code,
                        'qmt_code': code_convert(code),
                        'name': name,
                    })

        if eligible:
            print(f"  在线获取成功: {len(eligible)} 只（共 {len(all_sec)} 条记录）")
            # 缓存到本地
            _cache_stock_list(eligible, len(all_sec))
            return eligible

    # ═══ 3. 兜底：用已知代码范围 + 已有缓存文件 ═══
    print("  在线获取失败，使用已有缓存文件建立股票列表...")

    # 从 backtest/cache/ 中读取已有的 CSV 文件
    cached_files = set()
    if os.path.isdir(CACHE_DIR):
        cached_files = set(os.listdir(CACHE_DIR))

    eligible = []
    for mkt, start, end in KNOWN_STOCK_PREFIXES:
        for i in range(int(start) * 1000, int(end) * 1000 + 999):
            bs_code = f"{mkt}{i}"
            qmt_code = code_convert(bs_code)
            name = '未知'

            # 如果缓存中有这个文件，说明是真实存在的股票
            if qmt_code + '.csv' in cached_files:
                eligible.append({
                    'bs_code': bs_code,
                    'qmt_code': qmt_code,
                    'name': name,
                })

    if eligible:
        print(f"  从缓存文件识别出 {len(eligible)} 只股票")
        _cache_stock_list(eligible, 0)
        return eligible

    # 如果啥都没有，返回空
    print("[警告] 无法获取股票列表！")
    return []


def _cache_stock_list(stocks: list, total_count: int):
    """缓存股票列表到 JSON 文件"""
    try:
        with open(STOCK_LIST_CACHE, 'w', encoding='utf-8') as f:
            json.dump({
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'total_count': total_count,
                'eligible_count': len(stocks),
                'stocks': stocks,
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 获取单只股票行情数据
# ═══════════════════════════════════════════════════════════════

def load_stock_data(bs_code: str, qmt_code: str) -> pd.DataFrame:
    """
    读取股票近30天的日线数据（收盘价+成交额）。

    优先从本地 CSV 缓存加载（速度最快），
    缓存中没有则从 baostock 下载。

    返回: DataFrame [date, close, amount]，至少 21 行才有效
    """
    cache_path = os.path.join(CACHE_DIR, qmt_code + '.csv')

    # -- 1. 从本地 CSV 缓存加载 --
    if os.path.exists(cache_path):
        try:
            df = None
            for enc in ['utf-8', 'gbk']:
                try:
                    df = pd.read_csv(cache_path, encoding=enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if df is None or df.empty:
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

            # 读最近30天
            result = pd.DataFrame()
            result['date'] = df[date_col]

            # 收盘价
            close_col = 'close' if 'close' in df.columns else None
            if close_col:
                result['close'] = pd.to_numeric(df[close_col], errors='coerce')

            # 成交额
            if 'amount' in df.columns:
                result['amount'] = pd.to_numeric(df['amount'], errors='coerce')
            elif 'volume' in df.columns:
                result['amount'] = pd.to_numeric(df['volume'], errors='coerce') * \
                                   pd.to_numeric(df.get('close', 0), errors='coerce') * 100
            else:
                return pd.DataFrame()

            result = result.tail(30).dropna(subset=['close', 'amount'])
            result = result[result['amount'] > 0]
            return result

        except Exception:
            return pd.DataFrame()

    # -- 2. 从 baostock 下载（缓存中没有时）--
    try:
        import baostock as bs
        bs.login()

        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

        rs = bs.query_history_k_data_plus(
            bs_code, 'date,close,amount',
            start_date=start, end_date=end,
            frequency='d', adjustflag='3')

        rows = []
        while (rs.error_code == '0') & rs.next():
            row = rs.get_row_data()
            try:
                rows.append({
                    'date': row[0],
                    'close': float(row[1]) if row[1] else np.nan,
                    'amount': float(row[2]) if row[2] else np.nan,
                })
            except ValueError:
                continue

        bs.logout()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df[df['amount'] > 0].dropna(subset=['close'])
        return df.tail(30)

    except Exception:
        return pd.DataFrame()


def calc_alpha144(df: pd.DataFrame) -> (float, int):
    """
    计算单只股票的 Alpha#144 因子值。

    公式: alpha_144 = Σ(|ret_i| / amount_i)
          对过去20天中所有下跌日 ret_i < 0 求和

    参数:
      df — 至少 21 行有效数据的 DataFrame

    返回:
      (因子值, 下跌天数) — 因子值越大流动性冲击越大
      数据不足或无下跌日返回 (None, 0)
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


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("  Alpha#144 流动性冲击 — 沪深主板全市场筛选器")
    print("  公式: Σ(|跌幅|÷成交额) for ret<0, 过去20天")
    print("  范围: 沪市主板+深市主板+中小板（不含创业板/科创板/ST）")
    print("=" * 80)
    print()

    # -- 1. 获取股票列表 --
    print("* 步骤1: 获取沪深主板股票列表...")
    stocks = get_stock_list()
    if not stocks:
        print("[错误] 无法获取股票列表，退出。")
        return
    print(f"  OK 共 {len(stocks)} 只")
    print()

    # -- 2. 逐只计算因子 --
    print("* 步骤2: 逐只计算 Alpha#144 因子值...")
    print(f"  （共 {len(stocks)} 只，已有缓存文件约 {len(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else 0} 只）")
    print()

    results = []
    success = 0
    skipped = 0
    n_stocks = len(stocks)

    # 每 10% 报告一次进度
    batch_size = max(1, n_stocks // 10)
    next_report = batch_size

    for i, stock in enumerate(stocks):
        # -- 显示进度 --
        if i + 1 >= next_report or i == 0 or i == n_stocks - 1:
            pct = (i + 1) / n_stocks * 100
            print(f"    进度: {i+1}/{n_stocks} ({pct:.0f}%) "
                  f"| 有效: {success} | 跳过: {skipped}")
            next_report += batch_size

        # -- 读取行情数据 --
        df = load_stock_data(stock['bs_code'], stock['qmt_code'])
        if len(df) < FACTOR_WINDOW + 1:
            skipped += 1
            continue

        # -- 成交额过滤（日均 > 3000万）--
        if df['amount'].tail(FACTOR_WINDOW).mean() < MIN_DAILY_AMOUNT:
            skipped += 1
            continue

        # -- 计算因子 --
        alpha, neg_days = calc_alpha144(df)
        if alpha is None:
            skipped += 1
            continue

        results.append({
            'code': stock['qmt_code'],
            'name': stock['name'],
            'alpha': alpha,
            'neg_days': neg_days,
            'avg_amount': df['amount'].tail(FACTOR_WINDOW).mean(),
        })
        success += 1

    print(f"\n  OK 完成！有效: {success} | 跳过: {skipped}（数据不足/流动性差/无下跌日）")
    print()

    if not results:
        print("[错误] 没有成功计算任何股票的因子值。")
        return

    # -- 3. 排名 --
    results.sort(key=lambda x: x['alpha'], reverse=True)
    n = len(results)
    top_n = max(1, int(n * TOP_BOTTOM_PCT))
    bottom_n = max(1, int(n * TOP_BOTTOM_PCT))

    alphas = [r['alpha'] for r in results]
    print(f"* 步骤3: 排名统计")
    print(f"  +----------------------┬--------------+")
    print(f"  | 有效股票数            | {n:<13} |")
    print(f"  | 因子均值              | {np.mean(alphas):<13.2e} |")
    print(f"  | 因子中位数            | {np.median(alphas):<13.2e} |")
    print(f"  | 因子标准差            | {np.std(alphas):<13.2e} |")
    print(f"  +----------------------┴--------------+")
    print()

    # -- 格式化输出 --
    def print_stock_list(data_list, title, n_items):
        """通用打印函数"""
        print("=" * 80)
        print(f"  【{title}】 (Top {n_items})")
        print("=" * 80)
        print(f"  {'排名':<4} {'代码':<11} {'名称':<9} "
              f"{'因子值':<16} {'下跌日':<6} {'日均成交额(万)':<12}")
        print(f"  {'-'*58}")

        for rank, r in enumerate(data_list[:n_items], 1):
            avg_amt = r['avg_amount'] / 1e4
            # 截断中文名到 4 个中文字符宽度（8个占位）
            name = r['name'][:4] if len(r['name']) > 4 else r['name']
            print(f"  {rank:<4} {r['code']:<11} {name:<9} "
                  f"{r['alpha']:<16.4e} {r['neg_days']:<6} {avg_amt:<12.0f}")
        print()

    # -- 前 20%（流动性冲击最大）--
    print_stock_list(results, f"前 {TOP_BOTTOM_PCT*100:.0f}% — 流动性冲击最大 → 超跌反弹潜力最强", top_n)
    print(f"  >> 因子值越大 → 下跌时接盘资金越稀缺，恐慌抛售越极端")
    print(f"     一旦企稳突破，超跌反弹的空间和概率都更大")
    print()

    # -- 后 20%（流动性冲击最小）--
    print_stock_list(results[-bottom_n:][::-1],
                     f"后 {TOP_BOTTOM_PCT*100:.0f}% — 流动性冲击最小 → 筹码结构最健康", bottom_n)
    print(f"  >> 因子值越小 → 下跌时有充足的资金承接，流动性好")
    print(f"     筹码结构健康，但缺乏因流动性枯竭产生的超跌机会")
    print()

    # -- 综合信息 --
    print("=" * 80)
    print(f"  筛选日期:  {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  数据范围:  过去 {FACTOR_WINDOW} 个交易日")
    print(f"  最低日均额: {MIN_DAILY_AMOUNT/1e4:.0f} 万")
    print(f"  股票池:    沪深主板+中小板（实际计算 {success}, 跳过 {skipped}）")
    print("=" * 80)

    # -- 保存完整排名到 CSV --
    csv_path = os.path.join(
        SCRIPT_DIR,
        f'alpha144_全市场排名_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    )
    df_out = pd.DataFrame(results)
    df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n  完整排名已保存: {csv_path}")


if __name__ == '__main__':
    main()
