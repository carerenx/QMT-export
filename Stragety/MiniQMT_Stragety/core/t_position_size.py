# -*- coding: utf-8 -*-
"""Size new T legs in whole trading units; closing legs keep actual quantities."""
import math


def calculate_t_shares(price, available_base, min_lot, target_value,
                       position_fraction, available_cash=None):
    if (not math.isfinite(price) or price <= 0 or min_lot <= 0 or
            target_value <= 0 or not 0 < position_fraction <= 1):
        return 0
    available = max(0, int(available_base)) // min_lot * min_lot
    if available < min_lot:
        return 0
    # 金额是目标而非硬上限：高价股至少一手；底仓仅一手时允许用这一手。
    by_value = max(min_lot, int(target_value / price / min_lot) * min_lot)
    by_position = max(min_lot, int(available * position_fraction / min_lot) * min_lot)
    shares = min(available, by_value, by_position)
    if available_cash is not None:
        if not math.isfinite(available_cash) or available_cash <= 0:
            return 0
        # 留1%现金余量；最终仍受券商资金/委托结果约束。
        by_cash = int(available_cash / (price * 1.01) / min_lot) * min_lot
        shares = min(shares, by_cash)
    return shares
