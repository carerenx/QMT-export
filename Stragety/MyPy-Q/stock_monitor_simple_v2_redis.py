# -*- coding: utf-8 -*-
"""
极简股价监控 v2 — 基于 BigQMT Redis 行情接口
==============================================
数据源: BigQMT Redis bridge (xtdata.get_full_tick)
依赖: bigqmt_signal_trader (xtquant_compat)
运行前提: 启动 BigQMT Redis bridge 服务

默认监控: 长飞光纤 (601869)
修改 STOCK_LIST 增删股票
"""

import os
import sys
import time
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
#  >>> 修改这里 <<<
# ═══════════════════════════════════════════════════════════════
STOCK_LIST = [
    "000001.SH",  # 上证指数
    "601869.SH",  #
    "600487.SH",  #
    "600667.SH",  #
    "600105.SH",  #
    "688825.SH",  #
    "002859.SZ",  #
    "601991.SH",
    "600584.SH",
]

REFRESH_INTERVAL = 1   # 刷新间隔(秒)
# ═══════════════════════════════════════════════════════════════


# ── BigQMT Redis bridge 初始化 ──────────────────────────────

def init_bigqmt():
    """连接 BigQMT Redis bridge，返回 xtdata 客户端"""
    # 将 bigqmt bridge 源码加入 sys.path
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    bridge_src = os.path.join(project_root, "integrations", "bigqmt", "src")
    if bridge_src not in sys.path:
        sys.path.insert(0, bridge_src)

    from bigqmt_signal_trader.xtquant_compat import configure, load_client_config

    config = load_client_config()
    account_id = str(config.get("account_id") or "")
    if not account_id:
        print("[ERROR] BigQMT account_id 未配置，请检查 private client config")
        sys.exit(1)

    trader, xtdata = configure(account_id=account_id)
    print("[OK] BigQMT Redis bridge 已连接 (account={})".format(account_id))
    return xtdata


# ── 行情获取 ────────────────────────────────────────────────

def fetch_prices(xtdata, codes):
    """通过 BigQMT Redis 获取实时行情
    返回 {code: {name, price, change_pct, open, high, low, volume, amount}}
    """
    # 分批请求，避免单次请求过大
    chunk_size = 100
    all_ticks = {}
    for offset in range(0, len(codes), chunk_size):
        batch = codes[offset:offset + chunk_size]
        ticks = xtdata.get_full_tick(batch) or {}
        all_ticks.update(ticks)

    result = {}
    for code in codes:
        tick = all_ticks.get(code)
        if not tick:
            continue

        last_price = tick.get("lastPrice", 0) or 0
        open_price = tick.get("open", 0) or 0
        # 上一交易日收盘价 (用于计算涨跌幅)
        pre_close = tick.get("lastClose", 0) or tick.get("preClose", 0) or 0

        change_pct = 0.0
        if pre_close > 0:
            change_pct = (last_price / pre_close - 1.0) * 100.0

        # 获取股票名称
        name = tick.get("stockName", "")
        if not name:
            try:
                detail = xtdata.get_instrument_detail(code) or {}
                name = str(detail.get("InstrumentName") or code).strip()
            except Exception:
                name = code

        result[code] = {
            "name": name,
            "price": last_price,
            "change_pct": change_pct,
            "open": open_price,
            "high": tick.get("high", 0) or 0,
            "low": tick.get("low", 0) or 0,
            "volume": tick.get("volume", 0) or 0,
            "amount": tick.get("amount", 0) or 0,
        }

    return result


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def main():
    print("按 Ctrl+C 退出\n")
    time.sleep(0.3)

    # 初始化 BigQMT Redis bridge
    try:
        xtdata = init_bigqmt()
    except Exception as error:
        print("[ERROR] BigQMT Redis bridge 连接失败: {}".format(error))
        print("[ACTION] 请先启动 Big QMT Redis bridge 服务")
        sys.exit(1)

    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S")

            try:
                data = fetch_prices(xtdata, STOCK_LIST)
            except Exception as e:
                print(f"[{ts}] 获取失败: {e}")
                time.sleep(REFRESH_INTERVAL)
                continue

            print(f"[{ts}]", end="  ")
            for code in STOCK_LIST:
                q = data.get(code)
                if not q:
                    print(f"[{ts}] {code}  无数据")
                    continue
                chg = q["change_pct"]
                arrow = "+" if chg > 0 else ""
                print(f"{arrow}{q['price']:.2f}  ({chg:+.2f}%)", end="  ")
            print("")
            time.sleep(REFRESH_INTERVAL)

    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)


if __name__ == "__main__":
    main()
