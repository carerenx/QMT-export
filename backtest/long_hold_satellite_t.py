"""Frozen-v2 core plus protected intraday reverse-T satellite backtest."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "integrations/bigqmt/src"
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(ROOT))

from backtest.long_hold_rotation import _fetch
from backtest.long_hold_rotation import _maximum_drawdown
from backtest.long_hold_rotation import run_backtest
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import calculate_indicators
from Stragety.MiniQMT_Stragety.core.satellite_reverse_t import simulate_day


def run_hybrid(stock, daily_front, daily_raw, minute_raw, start, end,
               initial_cash=100000.0, initial_shares=1000, rate=0.0005):
    core = run_backtest(
        stock, daily_front.copy(), minute_raw.copy(), start, end,
        initial_cash=initial_cash, initial_shares=initial_shares,
        rate=rate, slippage_ticks=1, strategy_version="v2")
    dates = sorted({value[:8] for value in minute_raw.index if start <= value[:8] <= end})
    core_trades = {trade["execution_date"]: trade for trade in core["trades"]}
    decisions = {row["execution_date"]: row for row in core["decisions"]}
    cash = float(initial_cash)
    position = int(initial_shares)
    satellite_trades = []
    cycles = []
    equity_curve = []
    core_floor_violations = 0
    for date in dates:
        core_trade = core_trades.get(date)
        if core_trade:
            cash -= core_trade["shares"] * core_trade["price"] + core_trade["fee"]
            position += core_trade["shares"]
        decision = decisions.get(date)
        bars = minute_raw.loc[minute_raw.index.str[:8] == date]
        if decision and not bars.empty:
            completed = daily_raw.loc[daily_raw.index.astype(str) < date]
            if len(completed) >= 140:
                indicators = calculate_indicators(
                    completed.high.tolist(), completed.low.tolist(),
                    completed.close.tolist())
                t_result = simulate_day(
                    bars, completed.close.tolist(), indicators["atr20"],
                    position, decision["regime"], decision["risk_state"],
                    fee_rate=rate)
                floor = position
                for trade in t_result["trades"]:
                    cash -= trade["shares"] * trade["price"] + trade["fee"]
                    position += trade["shares"]
                    if position < floor:
                        allowed = abs(t_result["trades"][0]["shares"])
                        if position < floor - allowed:
                            core_floor_violations += 1
                    record = dict(trade)
                    record["date"] = date
                    record["regime"] = decision["regime"]
                    record["risk_state"] = decision["risk_state"]
                    record["position"] = position
                    record["cash"] = cash
                    satellite_trades.append(record)
                if t_result["trades"]:
                    cycles.append({
                        "date": date,
                        "regime": decision["regime"],
                        "gross": t_result["gross"],
                        "fees": t_result["fees"],
                        "net": t_result["gross"] - t_result["fees"],
                    })
        close_price = float(bars.iloc[-1].close)
        equity_curve.append({
            "date": date,
            "equity": cash + position * close_price,
            "cash": cash,
            "shares": position,
        })
    final_equity = equity_curve[-1]["equity"]
    initial_equity = core["initial_equity"]
    net_profit = final_equity - initial_equity
    t_net = sum(item["net"] for item in cycles)
    return {
        "stock": stock,
        "start": dates[0],
        "end": dates[-1],
        "model": "FROZEN_V2_CORE_WITH_SATELLITE_REVERSE_T",
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "net_profit": net_profit,
        "core_net_profit": core["net_profit"],
        "incremental_t_net": t_net,
        "maximum_drawdown": _maximum_drawdown(
            [initial_equity] + [row["equity"] for row in equity_curve]),
        "fees": core["fees"] + sum(item["fees"] for item in cycles),
        "satellite_fees": sum(item["fees"] for item in cycles),
        "satellite_cycles": len(cycles),
        "satellite_wins": sum(item["net"] > 0 for item in cycles),
        "satellite_losses": sum(item["net"] <= 0 for item in cycles),
        "final_cash": cash,
        "final_shares": position,
        "core_floor_violations": core_floor_violations,
        "unclosed_satellite_legs": 0,
        "compatibility_hold_final": core["compatibility_hold_final"],
        "compatibility_excess": final_equity - core["compatibility_hold_final"],
        "fully_invested_hold_final": core["fully_invested_hold_final"],
        "fully_invested_excess": final_equity - core["fully_invested_hold_final"],
        "core_trades": core["trades"],
        "satellite_trades": satellite_trades,
        "cycles": cycles,
        "equity_curve": equity_curve,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks", nargs="+", default=["600584.SH", "600105.SH", "601869.SH"])
    parser.add_argument("--start", default="20250912")
    parser.add_argument("--end", default="20260911")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite existing result")
    from bigqmt_signal_trader.xtquant_compat import configure, load_client_config
    config = load_client_config()
    account_id = str(config.get("account_id") or "")
    trader, xtdata = configure(account_id=account_id)
    pong = trader.client.call("ping")
    if not pong.get("pong"):
        raise RuntimeError("Redis QMT bridge verification failed")
    warmup = (datetime.strptime(args.start, "%Y%m%d") - timedelta(days=300)).strftime("%Y%m%d")
    qmt_end = (datetime.strptime(args.end, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    xtdata.download_history_data2(args.stocks, "1d", warmup, qmt_end, dividend_type="front")
    xtdata.download_history_data2(args.stocks, "1d", warmup, qmt_end, dividend_type="none")
    xtdata.download_history_data2(args.stocks, "1m", args.start, qmt_end, dividend_type="none")
    daily_front = _fetch(xtdata, args.stocks, "1d", warmup, qmt_end, "front")
    daily_raw = _fetch(xtdata, args.stocks, "1d", warmup, qmt_end, "none")
    minute_raw = _fetch(xtdata, args.stocks, "1m", args.start, qmt_end, "none")
    results = {}
    for stock in args.stocks:
        results[stock] = run_hybrid(
            stock, daily_front[stock], daily_raw[stock], minute_raw[stock],
            args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({stock: {
        "net_profit": row["net_profit"],
        "incremental_t_net": row["incremental_t_net"],
        "maximum_drawdown": row["maximum_drawdown"],
        "cycles": row["satellite_cycles"],
    } for stock, row in results.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
