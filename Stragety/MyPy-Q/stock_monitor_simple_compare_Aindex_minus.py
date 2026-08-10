# -*- coding: utf-8 -*-
"""
极简股价监控 — 只显示时间和股价
================================
默认监控: 长飞光纤 (601869)
修改 STOCK_LIST 增删股票
"""

import os
import sys
import time
import urllib.request
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
#  >>> 修改这里 <<<
# ═══════════════════════════════════════════════════════════════
STOCK_LIST = [
    "sh000001",  # 上证指数
    "601869",  # 
    "600487",  # 
    "600667",  # 
    "688825",  # 
]

REFRESH_INTERVAL = 2   # 刷新间隔(秒)
# ═══════════════════════════════════════════════════════════════


def get_prefix(code):
    """6位代码 → 市场前缀；已含前缀的代码（如 sh000001）原样返回"""
    if len(code) > 6:          # 已带前缀，如 "sh000001"
        return ""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"

def raw_code(code):
    """去掉市场前缀，返回纯6位代码"""
    return code[-6:] if len(code) > 6 else code

def fetch_prices(codes):
    """拉取腾讯行情，返回 {code: {name, price, change_pct}}"""
    prefixed = [f"{get_prefix(c)}{c}" for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk")

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        try:
            result[code] = {
                "name": vals[1],
                "price": float(vals[3]) if vals[3] else 0,
                "change_pct": float(vals[32]) if vals[32] else 0,
            }
        except (ValueError, IndexError):
            continue
    return result


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def main():
    print("按 Ctrl+C 退出\n")
    time.sleep(0.3)

    try:
        while True:
            # clear_screen()
            ts = datetime.now().strftime("%H:%M:%S")

            try:
                data = fetch_prices(STOCK_LIST)
            except Exception:
                print(f"[{ts}] 获取失败，重试中...")
                time.sleep(REFRESH_INTERVAL)
                continue
            # 获取上证指数涨跌幅作为基准
            idx_data = data.get("000001")  # sh000001 → key 为 "000001"
            idx_chg = idx_data["change_pct"] if idx_data else 0

            print(f"[{ts}]", end="  ")
            for code in STOCK_LIST:
                q = data.get(raw_code(code))
                if not q:
                    print(f"[{ts}] {code}  无数据")
                    continue
                chg = q["change_pct"]
                arrow = "+" if chg > 0 else ""
                # 计算超额收益: 个股涨跌幅 - 上证指数涨跌幅
                if raw_code(code) != "000001":
                    excess = chg - idx_chg
                    a = "+" if excess > 0 else ""
                    print(f"{arrow}{q['price']:.2f}  ({chg:+.2f}% 超{a}{excess:+.2f}%)", end="  ")
                else:
                    print(f"{arrow}{q['price']:.2f}  ({chg:+.2f}%)", end="  ")
            print("")
            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)


if __name__ == "__main__":
    main()
