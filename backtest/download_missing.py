"""
批量下载缺失的股票缓存数据，带超时和重试
"""
import os
import sys
import socket
import time
import logging

socket.setdefaulttimeout(20)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.config import STOCK_POOL, BENCHMARK_CODE, CACHE_DIR
from backtest.data_source import fetch_single_stock, bs_login
import baostock as bs


def main():
    codes = STOCK_POOL + [BENCHMARK_CODE]
    cached = set(f.replace('.csv', '') for f in os.listdir(CACHE_DIR) if f.endswith('.csv'))
    missing = sorted(set(c for c in codes if c not in cached))

    print(f"共 {len(codes)} 股票, 已缓存 {len(cached)}, 需下载 {len(missing)}")
    if not missing:
        print("所有数据已就绪!")
        return

    success = 0
    fail_list = []
    for i, code in enumerate(missing):
        print(f"[{i+1}/{len(missing)}] {code} ... ", end='', flush=True)

        try:
            df = fetch_single_stock(code, '2020-01-01', '2025-12-31', CACHE_DIR)
            if df is not None and len(df) > 0:
                print(f"OK ({len(df)}行)")
                success += 1
            else:
                print("FAIL (无数据)")
                fail_list.append(code)
        except Exception as e:
            print(f"FAIL: {e}")
            fail_list.append(code)

        # 每 5 只股票后暂停 3 秒
        if (i + 1) % 5 == 0:
            time.sleep(3)
        else:
            time.sleep(0.8)

    print(f"\n完成: 成功 {success}, 失败 {len(fail_list)}")
    if fail_list:
        print(f"失败列表: {fail_list}")
        # 重试一次
        print("\n--- 重试失败股票 (间隔更长) ---")
        retry_success = 0
        retry_fail = []
        for i, code in enumerate(fail_list):
            print(f"[重试 {i+1}/{len(fail_list)}] {code} ... ", end='', flush=True)
            time.sleep(5)  # 长间隔
            try:
                # 重新登录
                try:
                    bs.logout()
                except Exception:
                    pass
                time.sleep(1)
                bs_login()
                time.sleep(1)
                df = fetch_single_stock(code, '2020-01-01', '2025-12-31', CACHE_DIR)
                if df is not None and len(df) > 0:
                    print(f"OK ({len(df)}行)")
                    retry_success += 1
                else:
                    print("FAIL")
                    retry_fail.append(code)
            except Exception as e:
                print(f"FAIL: {e}")
                retry_fail.append(code)

        print(f"\n重试结果: 成功 {retry_success}, 失败 {len(retry_fail)}")
        if retry_fail:
            print(f"最终失败: {retry_fail}")

    print(f"缓存目录: {CACHE_DIR}, 共 {len(os.listdir(CACHE_DIR))} 个文件")


if __name__ == '__main__':
    main()
