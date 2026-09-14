"""Redis QMT backtest for the parameterized single-stock allocation strategy."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "integrations/bigqmt/src"
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(ROOT))

from Stragety.MiniQMT_Stragety.core.long_hold_allocation import decide_allocation, target_order
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import calculate_indicators
from Stragety.MiniQMT_Stragety.core.long_hold_allocation import classify_regime
from Stragety.MiniQMT_Stragety.core import long_hold_allocation_v2
from Stragety.MiniQMT_Stragety.core import long_hold_allocation_v3


def _hash_frame(frame):
    return hashlib.sha256(frame.to_csv(lineterminator="\n").encode("utf-8")).hexdigest()


def _maximum_drawdown(equities):
    peak = equities[0]
    maximum = 0.0
    for value in equities:
        peak = max(peak, value)
        maximum = max(maximum, 1.0 - value / peak)
    return maximum


def _cagr(initial, final, first_date, last_date):
    years = max(1.0 / 365.25, (last_date - first_date).days / 365.25)
    return (final / initial) ** (1.0 / years) - 1.0


def _execution_row(minute, date):
    rows = minute.loc[(minute.index.str[:8] == date) & (minute.index.str[8:14] >= "094000")]
    return rows.iloc[0] if not rows.empty else None


def run_backtest(stock, daily_front, minute_raw, start, end, initial_cash=100000.0,
                 initial_shares=1000, rate=0.0005, slippage_ticks=1,
                 tick_size=0.01, strategy_version="v1"):
    dates = sorted(set(minute_raw.index.str[:8]))
    dates = [date for date in dates if start <= date <= end]
    if not dates:
        raise ValueError("no minute sessions in requested interval")
    daily_front = daily_front.sort_index()
    cash = float(initial_cash)
    shares = int(initial_shares)
    first_row = _execution_row(minute_raw, dates[0])
    initial_price = float(first_row.open)
    initial_equity = cash + shares * initial_price
    equity_peak = initial_equity
    last_buy_date = None
    trades = []
    decisions = []
    equity_curve = []
    pending = None
    total_fees = 0.0
    active_regime = None
    previous_candidate = None
    candidate_streak = 0
    last_rebalance_session = None
    active_risk_mode = "NORMAL"
    risk_mode_age = 0
    for session_index, date in enumerate(dates):
        execution = _execution_row(minute_raw, date)
        if execution is None:
            continue
        execution_price = float(execution.open)
        if pending is not None:
            buy = pending["planned_share_delta"] > 0
            fill_price = execution_price + tick_size * slippage_ticks if buy else execution_price - tick_size * slippage_ticks
            if strategy_version in ("v2", "v3"):
                since_rebalance = None
                if last_rebalance_session is not None:
                    since_rebalance = session_index - last_rebalance_session
                order_module = (
                    long_hold_allocation_v3
                    if strategy_version == "v3"
                    else long_hold_allocation_v2)
                delta = order_module.target_order(
                    shares, cash, fill_price, pending["target_weight"],
                    sessions_since_rebalance=since_rebalance,
                    allow_buy=pending["risk_state"] != "HALT_BUYS")
            else:
                delta = target_order(
                    shares,
                    cash,
                    fill_price,
                    pending["target_weight"],
                    allow_buy=pending["risk_state"] != "HALT_BUYS",
                )
            if delta:
                value = abs(delta) * fill_price
                fee = max(5.0, value * rate)
                if delta > 0 and value + fee > cash:
                    affordable = int((cash - 5.0) / fill_price / 100) * 100
                    delta = max(0, min(delta, affordable))
                    value = delta * fill_price
                    fee = max(5.0, value * rate) if delta else 0.0
                if delta:
                    cash -= delta * fill_price + fee
                    shares += delta
                    total_fees += fee
                    if delta > 0:
                        last_buy_date = date
                    last_rebalance_session = session_index
                    trades.append({
                        "execution_date": date,
                        "execution_time": str(execution.name),
                        "signal_date": pending["signal_date"],
                        "shares": delta,
                        "price": fill_price,
                        "fee": fee,
                        "cash": cash,
                        "position": shares,
                        "target_weight": pending["target_weight"],
                        "reason_codes": pending["reason_codes"],
                    })
        close_rows = minute_raw.loc[minute_raw.index.str[:8] == date]
        close_price = float(close_rows.iloc[-1].close)
        equity = cash + shares * close_price
        equity_peak = max(equity_peak, equity)
        equity_drawdown = max(0.0, 1.0 - equity / equity_peak)
        current_weight = shares * close_price / equity if equity > 0 else 0.0
        equity_curve.append({"date": date, "equity": equity, "cash": cash, "shares": shares})
        if session_index + 1 >= len(dates):
            pending = None
            continue
        completed = daily_front.loc[daily_front.index.astype(str) <= date]
        if len(completed) < 140:
            pending = None
            continue
        sessions_since_buy = None
        if last_buy_date in dates:
            sessions_since_buy = session_index - dates.index(last_buy_date)
        if strategy_version in ("v2", "v3"):
            indicators = calculate_indicators(
                completed.high.tolist(), completed.low.tolist(),
                completed.close.tolist())
            candidate = classify_regime(indicators)[0]
            if candidate == previous_candidate:
                candidate_streak += 1
            else:
                previous_candidate = candidate
                candidate_streak = 1
            if strategy_version == "v3":
                pending = long_hold_allocation_v3.decide_allocation(
                    completed.high.tolist(), completed.low.tolist(),
                    completed.close.tolist(), current_weight, equity_drawdown,
                    date, dates[session_index + 1], active_regime,
                    candidate_streak, active_risk_mode, risk_mode_age)
                next_risk_mode = pending["risk_mode"]
                if next_risk_mode == active_risk_mode:
                    risk_mode_age += 1
                else:
                    active_risk_mode = next_risk_mode
                    risk_mode_age = 0
            else:
                pending = long_hold_allocation_v2.decide_allocation(
                    completed.high.tolist(), completed.low.tolist(),
                    completed.close.tolist(), current_weight, equity_drawdown,
                    date, dates[session_index + 1], active_regime,
                    candidate_streak)
            active_regime = pending["regime"]
            since_rebalance = None
            if last_rebalance_session is not None:
                since_rebalance = session_index - last_rebalance_session
            order_module = (
                long_hold_allocation_v3
                if strategy_version == "v3"
                else long_hold_allocation_v2)
            pending["planned_share_delta"] = order_module.target_order(
                shares, cash, close_price, pending["target_weight"],
                sessions_since_rebalance=since_rebalance,
                allow_buy=pending["risk_state"] != "HALT_BUYS")
        else:
            pending = decide_allocation(
                completed.high.tolist(),
                completed.low.tolist(),
                completed.close.tolist(),
                current_weight,
                equity_drawdown,
                date,
                dates[session_index + 1],
                sessions_since_buy,
            )
            pending["planned_share_delta"] = target_order(
                shares,
                cash,
                close_price,
                pending["target_weight"],
                allow_buy=pending["risk_state"] != "HALT_BUYS",
            )
        decisions.append(pending.copy())
    final_price = float(minute_raw.loc[minute_raw.index.str[:8] == dates[-1]].iloc[-1].close)
    final_equity = cash + shares * final_price
    compatibility_final = initial_cash + initial_shares * final_price
    fully_invested_shares = int(initial_equity / initial_price / 100) * 100
    fully_invested_cash = initial_equity - fully_invested_shares * initial_price
    fully_invested_final = fully_invested_cash + fully_invested_shares * final_price
    first_date = datetime.strptime(dates[0], "%Y%m%d")
    last_date = datetime.strptime(dates[-1], "%Y%m%d")
    cagr = _cagr(initial_equity, final_equity, first_date, last_date)
    maximum_drawdown = _maximum_drawdown([row["equity"] for row in equity_curve])
    turnover = sum(abs(row["shares"] * row["price"]) for row in trades) / initial_equity
    requested_days = (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days
    covered_days = (last_date - first_date).days
    coverage_ratio = covered_days / requested_days if requested_days > 0 else 1.0
    acceptance_failures = []
    if coverage_ratio < 0.90:
        acceptance_failures.append("REQUESTED_PERIOD_MINUTE_COVERAGE_INCOMPLETE")
    if cagr < 0.30:
        acceptance_failures.append("CAGR_BELOW_30_PERCENT")
    if maximum_drawdown > 0.30:
        acceptance_failures.append("MAX_DRAWDOWN_ABOVE_30_PERCENT")
    if maximum_drawdown > 0 and cagr / maximum_drawdown < 1.0:
        acceptance_failures.append("CALMAR_BELOW_1")
    if final_equity <= fully_invested_final:
        acceptance_failures.append("BELOW_FULLY_INVESTED_HOLD")
    return {
        "stock": stock,
        "strategy_version": strategy_version,
        "start": dates[0],
        "end": dates[-1],
        "requested_start": start,
        "requested_end": end,
        "coverage_ratio": coverage_ratio,
        "initial_cash": initial_cash,
        "initial_shares": initial_shares,
        "initial_equity": initial_equity,
        "final_cash": cash,
        "final_shares": shares,
        "final_equity": final_equity,
        "net_profit": final_equity - initial_equity,
        "cagr": cagr,
        "maximum_drawdown": maximum_drawdown,
        "calmar": cagr / maximum_drawdown if maximum_drawdown > 0 else None,
        "turnover": turnover,
        "fees": total_fees,
        "compatibility_hold_final": compatibility_final,
        "compatibility_excess": final_equity - compatibility_final,
        "fully_invested_hold_final": fully_invested_final,
        "fully_invested_excess": final_equity - fully_invested_final,
        "acceptance": "PASS" if not acceptance_failures else "FAIL",
        "acceptance_failures": acceptance_failures,
        "trades": trades,
        "decisions": decisions,
        "equity_curve": equity_curve,
    }


def _fetch(xtdata, stocks, period, start, end, dividend_type):
    return xtdata.get_market_data_ex(
        field_list=["open", "high", "low", "close", "volume", "amount"],
        stock_list=stocks,
        period=period,
        start_time=start,
        end_time=end,
        count=-1,
        dividend_type=dividend_type,
        fill_data=False,
        chunk_size=0,
        timeout_seconds=120,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks", nargs="+", default=["600584.SH", "600105.SH"])
    parser.add_argument("--start", default="20250912")
    parser.add_argument("--end", default="20260911")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rate", type=float, default=0.0005)
    parser.add_argument("--slippage-ticks", type=int, default=1)
    parser.add_argument(
        "--strategy-version", choices=("v1", "v2", "v3"), default="v1")
    args = parser.parse_args()
    from bigqmt_signal_trader.xtquant_compat import configure, load_client_config
    config = load_client_config()
    account_id = str(config.get("account_id") or "")
    trader, xtdata = configure(account_id=account_id)
    pong = trader.client.call("ping")
    if not pong.get("pong") or str(pong.get("account_id")) != account_id:
        raise RuntimeError("Redis QMT bridge account verification failed")
    warmup = (datetime.strptime(args.start, "%Y%m%d") - timedelta(days=300)).strftime("%Y%m%d")
    qmt_end = (datetime.strptime(args.end, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    xtdata.download_history_data2(args.stocks, "1d", warmup, qmt_end, dividend_type="front")
    xtdata.download_history_data2(args.stocks, "1m", args.start, qmt_end, dividend_type="none")
    daily = _fetch(xtdata, args.stocks, "1d", warmup, qmt_end, "front")
    minute = _fetch(xtdata, args.stocks, "1m", args.start, qmt_end, "none")
    results = {}
    for stock in args.stocks:
        result = run_backtest(stock, daily[stock].copy(), minute[stock].copy(), args.start, args.end,
                              rate=args.rate, slippage_ticks=args.slippage_ticks,
                              strategy_version=args.strategy_version)
        result["provenance"] = {
            "source": "BigQMT Redis RPC",
            "rpc_revision": pong.get("rpc_revision"),
            "daily_front_sha256": _hash_frame(daily[stock]),
            "minute_raw_sha256": _hash_frame(minute[stock]),
            "daily_rows": len(daily[stock]),
            "minute_rows": len(minute[stock]),
        }
        results[stock] = result
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({stock: {key: value for key, value in result.items() if key in (
        "cagr", "maximum_drawdown", "calmar", "net_profit", "compatibility_excess",
        "fully_invested_excess", "fees", "final_shares")}
        for stock, result in results.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
