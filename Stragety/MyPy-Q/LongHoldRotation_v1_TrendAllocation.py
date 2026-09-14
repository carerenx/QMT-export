# -*- coding: utf-8 -*-
"""Parameterized single-stock long-hold allocation strategy (research only)."""
from __future__ import print_function

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Stragety.MiniQMT_Stragety.core import config as cfg
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import decide_allocation, target_order


ACCOUNT = cfg.ACCOUNT
DEFAULT_STOCK = "600584.SH"
STATE_DIR = Path(__file__).resolve().parent / "state"


def _frame_values(frame, field):
    return [float(value) for value in frame[field].tolist()]


def _atomic_save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(temporary), str(path))


def build_frozen_plan(frame, stock, cash, shares, equity_peak, last_buy_date=""):
    if frame is None or len(frame) < 140:
        raise ValueError("QMT must provide at least 140 completed front-adjusted daily bars")
    signal_date = str(frame.index[-1])[:8]
    execution_date = (datetime.strptime(signal_date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    price = float(frame["close"].iloc[-1])
    equity = float(cash) + int(shares) * price
    drawdown = max(0.0, 1.0 - equity / float(equity_peak)) if equity_peak > 0 else 0.0
    sessions_since_buy = None
    if last_buy_date:
        completed_dates = [str(value)[:8] for value in frame.index]
        if last_buy_date in completed_dates:
            sessions_since_buy = len(completed_dates) - 1 - completed_dates.index(last_buy_date)
    current_weight = shares * price / equity if equity > 0 else 0.0
    decision = decide_allocation(
        _frame_values(frame, "high"),
        _frame_values(frame, "low"),
        _frame_values(frame, "close"),
        current_weight,
        drawdown,
        signal_date,
        execution_date,
        sessions_since_buy,
    )
    allow_buy = decision["risk_state"] != "HALT_BUYS"
    order_delta = target_order(
        shares,
        cash,
        price,
        decision["target_weight"],
        allow_buy=allow_buy,
    )
    decision.update({
        "stock": stock,
        "reference_price": price,
        "planned_share_delta": order_delta,
        "equity": equity,
        "equity_peak": max(float(equity_peak), equity),
        "status": "FROZEN_FOR_NEXT_SESSION",
    })
    return decision


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", default=DEFAULT_STOCK)
    parser.add_argument("--cash", type=float, default=100000.0)
    parser.add_argument("--shares", type=int, default=1000)
    parser.add_argument("--equity-peak", type=float, default=0.0)
    parser.add_argument("--mode", choices=("signal", "live"), default="signal")
    args = parser.parse_args(argv)
    if args.mode == "live":
        raise RuntimeError("RESEARCH_ONLY: one-year acceptance has not passed")
    from xtquant import xtdata
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d")
    data = xtdata.get_market_data_ex(
        field_list=["open", "high", "low", "close", "volume", "amount"],
        stock_list=[args.stock],
        period="1d",
        start_time=start,
        end_time=today,
        count=-1,
        dividend_type="front",
        fill_data=False,
    )
    frame = data.get(args.stock)
    peak = args.equity_peak or (args.cash + args.shares * float(frame["close"].iloc[-1]))
    plan = build_frozen_plan(frame, args.stock, args.cash, args.shares, peak)
    state_path = STATE_DIR / "long_hold_v1_{}_{}.json".format(ACCOUNT, args.stock.replace(".", "_"))
    _atomic_save(state_path, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
