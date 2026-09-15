# -*- coding: utf-8 -*-
"""v39 non-MOM intraday T strategy running through BigQMT Redis RPC."""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Stragety.RedisQMT.Common import config
from Stragety.RedisQMT.Common.logger import build_logger
from Stragety.RedisQMT.Common.redis_qmt import RedisQmtAdapter
from Stragety.RedisQMT.Common.signals import compute_signal
from Stragety.RedisQMT.Common.turning_points import TurningPointTracker


class StrategyRunner(object):
    def __init__(self, adapter=None, logger=None):
        self.adapter = adapter or RedisQmtAdapter(False)
        self.logger = logger or build_logger("DT_v1")
        self.state = "IDLE"
        self.signal = {}
        self.cash = 0.0
        self.sellable_shares = 0
        self.short_legs = []
        self.long_legs = []
        self.trade_count_short = 0
        self.trade_count_long = 0
        self.buyback_target = 0.0
        self.sell_fill_price = 0.0
        self.sellback_target = 0.0
        self.ladder_sell_target = 0.0
        self.ladder_buy_target = 0.0
        self.limit_guard_active = False
        self.limit_release_since = 0.0
        self.turning = None

    def start_day(self, signal, cash, sellable_shares):
        self.signal = dict(signal or {})
        self.cash = float(cash or 0)
        self.sellable_shares = max(0, int(sellable_shares or 0))
        self.state = "IDLE"
        self.short_legs = []
        self.long_legs = []
        self.trade_count_short = 0
        self.trade_count_long = 0
        self.buyback_target = 0.0
        self.sell_fill_price = 0.0
        self.sellback_target = 0.0
        self.ladder_sell_target = 0.0
        self.ladder_buy_target = 0.0
        self.limit_guard_active = False
        self.limit_release_since = 0.0
        self.turning = None

    def process_tick(self, price, now_hms=None, last_close=0.0):
        price = float(price or 0)
        if price <= 0:
            return None
        now_hms = now_hms or datetime.now().strftime("%H:%M:%S")
        self._update_limit_guard(price, float(last_close or 0), time.time())
        if (config.ENABLE_FORCE_CLOSE and now_hms >= config.FORCE_CLOSE_TIME
                and self.state not in ("IDLE", "DONE", "FORCED")):
            return self._force_close(price)
        handlers = {
            "IDLE": self._handle_idle,
            "REV_SPIKING": self._handle_reverse_spiking,
            "REV_SOLD": self._handle_reverse_sold,
            "REV_DIPPING": self._handle_reverse_dipping,
            "FWD_DIPPING": self._handle_forward_dipping,
            "FWD_BOUGHT": self._handle_forward_bought,
            "FWD_SPIKING": self._handle_forward_spiking,
        }
        handler = handlers.get(self.state)
        return handler(price) if handler else None

    def _new_leg_allowed(self):
        return not self.limit_guard_active

    def _handle_idle(self, price):
        if not self._new_leg_allowed():
            return None
        if (self.signal.get("do_short") and
                self.trade_count_short < config.MAX_DAILY_TRADES and
                self.sellable_shares >= config.TRADE_LOT_SIZE and
                price >= float(self.signal.get("sell_trigger", float("inf")))):
            self.trade_count_short += 1
            self.state = "REV_SPIKING"
            self.turning = TurningPointTracker("peak", config.PULLBACK_PCT, "SELL")
            self.turning.arm(price)
            return None
        buy_trigger = float(self.signal.get("buy_trigger", 0) or 0)
        if (self.signal.get("do_long", False) and buy_trigger > 0 and
                self.trade_count_long < config.MAX_DAILY_TRADES and
                self._long_capacity(price) >= config.TRADE_LOT_SIZE and
                price <= buy_trigger):
            self.trade_count_long += 1
            self.state = "FWD_DIPPING"
            self.turning = TurningPointTracker("dip", config.BOUNCE_PCT, "BUY")
            self.turning.arm(price)
        return None

    def _handle_reverse_spiking(self, price):
        if self.turning.update(price) != "SELL":
            return None
        result = self._submit("SELL", config.TRADE_LOT_SIZE, price, "reverse-t-sell")
        filled = result["filled_shares"]
        if filled <= 0:
            self.trade_count_short -= 1
            self.state = "IDLE"
            return result
        fill_price = result["average_price"]
        self.sellable_shares -= filled
        self.sell_fill_price = fill_price
        self.short_legs.append((fill_price, filled))
        self.buyback_target = round(
            fill_price * (1.0 - float(self.signal["atr_pct"]) *
                          config.BUYBACK_TRIGGER_MULT), 2)
        self.ladder_sell_target = round(fill_price * (1.0 + config.LADDER_UP_STEP_PCT), 2)
        self.state = "REV_SOLD"
        return result

    def _handle_reverse_sold(self, price):
        if (self.ladder_sell_target and price >= self.ladder_sell_target and
                self.trade_count_short < config.MAX_DAILY_TRADES and
                self.sellable_shares >= config.TRADE_LOT_SIZE and self._new_leg_allowed()):
            self.trade_count_short += 1
            self.state = "REV_SPIKING"
            self.turning = TurningPointTracker("peak", config.PULLBACK_PCT, "SELL")
            self.turning.arm(price)
            return None
        if (config.ENABLE_EMERGENCY_BUYBACK and
                price >= self.sell_fill_price * (1.0 + config.EMERGENCY_BUYBACK_PCT)):
            return self._close_reverse(price, "emergency-buyback")
        rebound = self.sell_fill_price * config.REV_REBOUND_BUYBACK_RATIO
        if price <= self.buyback_target or price <= rebound:
            self.state = "REV_DIPPING"
            self.turning = TurningPointTracker("dip", config.BOUNCE_PCT, "BUY")
            self.turning.arm(price)
        return None

    def _handle_reverse_dipping(self, price):
        if self.turning.update(price) == "BUY":
            return self._close_reverse(price, "reverse-t-buyback")
        return None

    def _close_reverse(self, price, remark):
        shares = sum(item[1] for item in self.short_legs)
        if shares % config.TRADE_LOT_SIZE:
            self.state = "RECOVERY_REQUIRED"
            self.logger.error(
                "reverse T has %s unmatched shares; automatic buy is blocked by whole-lot rules",
                shares)
            return {"status": "RECOVERY_REQUIRED", "filled_shares": 0,
                    "average_price": 0.0, "order_id": None, "virtual": False}
        result = self._submit("BUY", shares, price, remark)
        filled = result["filled_shares"]
        self._consume_legs(self.short_legs, filled)
        self.cash -= filled * result["average_price"]
        if self.short_legs:
            remaining = sum(item[1] for item in self.short_legs)
            self.state = ("RECOVERY_REQUIRED" if remaining % config.TRADE_LOT_SIZE
                          else "REV_SOLD")
        else:
            self.state = "DONE"
            self.ladder_sell_target = 0.0
        return result

    def _handle_forward_dipping(self, price):
        if self.turning.update(price) != "BUY":
            return None
        shares = min(config.TRADE_LOT_SIZE, self._long_capacity(price))
        result = self._submit("BUY", shares, price, "forward-t-buy")
        filled = result["filled_shares"]
        if filled <= 0:
            self.trade_count_long -= 1
            self.state = "IDLE" if not self.long_legs else "FWD_BOUGHT"
            return result
        fill_price = result["average_price"]
        self.cash -= filled * fill_price
        self.long_legs.append((fill_price, filled))
        average = self._average_price(self.long_legs)
        self.sellback_target = round(average * (1.0 + config.SELLBACK_RISE_PCT), 2)
        self.ladder_buy_target = round(fill_price * (1.0 - config.LADDER_DOWN_STEP_PCT), 2)
        self.state = "FWD_BOUGHT"
        return result

    def _handle_forward_bought(self, price):
        average = self._average_price(self.long_legs)
        if average and price <= average * (1.0 - config.STOP_LOSS_PCT):
            return self._close_forward(price, "forward-t-stop-loss")
        if (self.ladder_buy_target and price <= self.ladder_buy_target and
                self.trade_count_long < config.MAX_DAILY_TRADES and
                self._long_capacity(price) >= config.TRADE_LOT_SIZE and
                self._new_leg_allowed()):
            self.trade_count_long += 1
            self.state = "FWD_DIPPING"
            self.turning = TurningPointTracker("dip", config.BOUNCE_PCT, "BUY")
            self.turning.arm(price)
            return None
        if price >= self.sellback_target:
            self.state = "FWD_SPIKING"
            self.turning = TurningPointTracker("peak", config.PULLBACK_PCT, "SELL")
            self.turning.arm(price)
        return None

    def _handle_forward_spiking(self, price):
        if self.turning.update(price) == "SELL":
            return self._close_forward(price, "forward-t-sellback")
        return None

    def _close_forward(self, price, remark):
        shares = sum(item[1] for item in self.long_legs)
        result = self._submit("SELL", shares, price, remark, allow_odd_lot=True)
        filled = result["filled_shares"]
        self._consume_legs(self.long_legs, filled)
        self.sellable_shares = max(0, self.sellable_shares - filled)
        self.cash += filled * result["average_price"]
        self.state = "FWD_BOUGHT" if self.long_legs else "DONE"
        if not self.long_legs:
            self.ladder_buy_target = 0.0
        return result

    def _force_close(self, price):
        if self.short_legs:
            result = self._close_reverse(price, "force-buyback")
        elif self.long_legs:
            result = self._close_forward(price, "force-sellback")
        else:
            result = None
        if not self.short_legs and not self.long_legs:
            self.state = "FORCED"
        return result

    def _submit(self, side, shares, price, remark, allow_odd_lot=False):
        shares = int(shares)
        if not allow_odd_lot:
            shares = shares // config.TRADE_LOT_SIZE * config.TRADE_LOT_SIZE
        if shares <= 0:
            return {"status": "SKIP", "filled_shares": 0,
                    "average_price": 0.0, "order_id": None, "virtual": True}
        result = self.adapter.submit(
            side, config.STOCK_QMT, shares, price, remark,
            allow_odd_lot=allow_odd_lot)
        self.logger.info("%s %s shares=%s price=%.2f status=%s",
                         remark, side, shares, price, result["status"])
        return result

    def _long_capacity(self, price):
        cash_lots = int(self.cash / (price * config.TRADE_LOT_SIZE * 1.01))
        sellable_lots = self.sellable_shares // config.TRADE_LOT_SIZE
        return min(cash_lots, sellable_lots) * config.TRADE_LOT_SIZE

    @staticmethod
    def _average_price(legs):
        shares = sum(item[1] for item in legs)
        return sum(price * volume for price, volume in legs) / shares if shares else 0.0

    @staticmethod
    def _consume_legs(legs, shares):
        remaining = int(shares)
        while legs and remaining > 0:
            price, volume = legs[0]
            used = min(volume, remaining)
            remaining -= used
            volume -= used
            if volume:
                legs[0] = (price, volume)
            else:
                legs.pop(0)

    def _update_limit_guard(self, price, last_close, now_ts):
        if last_close <= 0:
            return
        rise = price / last_close - 1.0
        if rise + 1e-12 >= config.LIMIT_UP_GUARD_PCT:
            self.limit_guard_active = True
            self.limit_release_since = 0.0
        elif self.limit_guard_active and rise <= config.LIMIT_UP_RELEASE_PCT:
            if not self.limit_release_since:
                self.limit_release_since = now_ts
            elif now_ts - self.limit_release_since >= config.LIMIT_UP_RELEASE_HOLD_SECONDS:
                self.limit_guard_active = False
                self.limit_release_since = 0.0
        elif self.limit_guard_active:
            self.limit_release_since = 0.0


def _history_columns(frame, today=None):
    columns = {name: [] for name in ("open", "high", "low", "close", "volume")}
    if frame is None:
        return columns
    for index, row in frame.iterrows():
        date_text = "".join(character for character in str(index) if character.isdigit())[:8]
        if today and date_text and date_text >= str(today):
            continue
        try:
            values = {name: float(row.get(name)) for name in columns}
        except (TypeError, ValueError):
            continue
        if all(value > 0 for value in values.values()):
            for name, value in values.items():
                columns[name].append(value)
    return columns


def run(mode):
    adapter = RedisQmtAdapter(live=(mode == "live"))
    adapter.verify()
    log_dir = Path(__file__).resolve().parent / "logs"
    logger = build_logger("DT_v1_live", log_dir)
    runner = StrategyRunner(adapter=adapter, logger=logger)
    active_date = ""
    while True:
        now = datetime.now()
        today = now.strftime("%Y%m%d")
        hms = now.strftime("%H:%M:%S")
        if not config.is_market_open(hms):
            time.sleep(5)
            continue
        snapshot = adapter.snapshot(config.STOCK_QMT)
        if active_date != today:
            columns = _history_columns(adapter.history(config.STOCK_QMT), today)
            signal = compute_signal(
                columns["open"], columns["high"], columns["low"],
                columns["close"], columns["volume"], snapshot["open"])
            if signal is None:
                raise RuntimeError("at least 60 valid completed daily bars are required")
            buy_trigger_floor = round(
                signal["open_price"] * (1.0 - config.BUY_TRIGGER_PCT), 2)
            buy_trigger_trail = round(
                snapshot["price"] * (1.0 - config.BUY_TRIGGER_TRAIL), 2)
            signal["buy_trigger_floor"] = buy_trigger_floor
            signal["buy_trigger_trail"] = buy_trigger_trail
            signal["buy_trigger"] = max(buy_trigger_floor, buy_trigger_trail)
            runner.start_day(signal, snapshot["cash"],
                             snapshot["sellable_shares"])
            active_date = today
        runner.process_tick(snapshot["price"], hms, snapshot["last_close"])
        time.sleep(config.LOOP_INTERVAL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="signal", choices=("signal", "live"))
    parser.add_argument("--check", action="store_true",
                        help="validate local RedisQMT client configuration only")
    parser.add_argument("--probe", action="store_true",
                        help="perform read-only Redis RPC, account, and quote checks")
    args = parser.parse_args(argv)
    if args.check:
        try:
            RedisQmtAdapter(live=False)
            print("RedisQMT local setup OK; server connectivity was not checked")
            return 0
        except Exception as error:
            print("[ERROR] RedisQMT local check failed: {}".format(error))
            return 2
    if args.probe:
        try:
            RedisQmtAdapter(live=False).verify()
            print("RedisQMT read-only probe passed")
            return 0
        except Exception as error:
            print("[ERROR] RedisQMT read-only probe failed: {}".format(error))
            return 2
    if args.mode == "live":
        print("LIVE trading: {} {} account {}".format(
            config.STOCK_NAME, config.STOCK_QMT, config.ACCOUNT))
        if input("Type yes to continue: ").strip().lower() != "yes":
            print("Cancelled")
            return 1
    try:
        run(args.mode)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print("[ERROR] {}".format(error))
        print("[ACTION] Start and verify the configured BigQMT Redis bridge.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
