"""Immutable strategy settings assembled at the versioned entrypoint."""
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySettings:
    lot_size: int = 100
    min_lot: int = 100
    max_daily_trades: int = 5
    pullback_pct: float = 0.001
    bounce_pct: float = 0.001
    buyback_trigger_mult: float = 0.15
    buyback_tighten_mult: float = 0.60
    emergency_buyback: bool = False
    emergency_buyback_pct: float = 0.03
    stop_loss_pct: float = 0.015
    buy_trigger_pct: float = 0.030
    buy_trigger_trail: float = 0.020
    sellback_rise_pct: float = 0.012
    sell_trigger_scale: float = 0.6
    daily_range_cap_enabled: bool = True
    daily_range_cap_mult: float = 0.80
    ladder_up_step_pct: float = 0.015
    ladder_down_step_pct: float = 0.015
    force_close_time: str = '14:57:00'
    lock_price_ratio: float = 0.015
    lock_momentum_pct: float = 0.005
    lock_drawdown_pct: float = 0.005
    lock_lookback_sec: int = 300
    lock_cooldown_sec: int = 120
    mom_enabled: bool = False
    mom_window_sec: int = 120
    mom_atr_window_sec: int = 600
    mom_atr_mult: float = 3.0
    mom_trigger_min_pct: float = 0.01
    mom_trigger_max_pct: float = 0.06
    mom_short_buyback_pct: float = 0.015
    mom_long_sellback_pct: float = 0.018
    mom_lot_size: int = 100
    mom_max_daily_trades: int = 3
    mom_emergency_buyback: bool = True
    market_status_interval_sec: int = 60
    ma20_risk_ratio: float = 0.97

    @classmethod
    def from_config(cls, cfg):
        return cls(
            lot_size=cfg.TRADE_LOT_SIZE,
            min_lot=cfg.MIN_LOT,
            max_daily_trades=cfg.MAX_DAILY_TRADES,
            pullback_pct=cfg.PULLBACK_PCT,
            bounce_pct=cfg.BOUNCE_PCT,
            buyback_trigger_mult=cfg.BUYBACK_TRIGGER_MULT,
            buyback_tighten_mult=cfg.BUYBACK_TIGHTEN_MULT,
            emergency_buyback=cfg.EMERGENCY_BUYBACK,
            emergency_buyback_pct=cfg.EMERGENCY_BUYBACK_PCT,
            stop_loss_pct=cfg.STOP_LOSS_PCT,
            buy_trigger_pct=cfg.BUY_TRIGGER_PCT,
            buy_trigger_trail=cfg.BUY_TRIGGER_TRAIL,
            sellback_rise_pct=cfg.SELLBACK_RISE_PCT,
            sell_trigger_scale=cfg.SELL_TRIGGER_SCALE,
            daily_range_cap_enabled=cfg.DAILY_RANGE_CAP_ENABLED,
            daily_range_cap_mult=cfg.DAILY_RANGE_CAP_MULT,
            force_close_time=cfg.FORCE_CLOSE_TIME,
            lock_price_ratio=cfg.LOCK_PRICE_RATIO,
            lock_momentum_pct=cfg.LOCK_MOMENTUM_PCT,
            lock_drawdown_pct=cfg.LOCK_DRAWDOWN_PCT,
            lock_lookback_sec=cfg.LOCK_LOOKBACK_SEC,
            lock_cooldown_sec=cfg.LOCK_COOLDOWN_SEC,
            mom_lot_size=cfg.TRADE_LOT_SIZE_MOM,
        )
