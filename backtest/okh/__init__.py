"""
backtest.okh — Adapted OSkhQuant (KHQuant) Core
=================================================
Portable backtesting engine components extracted from OSkhQuant v2.1.4,
adapted to work without PyQt5 GUI and xtquant/MiniQMT dependencies.

Original: https://github.com/khscience/OSkhQuant
License: CC BY-NC 4.0 (inherited from original)

Modules:
  indicators  — MyTT technical indicator library (MA, MACD, RSI, KDJ, BOLL, etc.)
  config      — JSON-based configuration management
  trade_mgr   — Order execution, trade cost calculation, position tracking
  risk_mgr    — Risk management checks
  engine      — Enhanced event-driven backtest loop
  adapter     — QMT strategy format → OSkhQuant strategy format bridge
  reporter    — Performance reporting and visualization
"""

from .indicators import (
    MA, EMA, SMA, WMA, DMA,
    MACD, KDJ, RSI, WR, BIAS, BOLL, PSY, CCI, ATR, BBI, DMI,
    HHV, LLV, REF, STD, SUM, CROSS,
)

__all__ = [
    'MA', 'EMA', 'SMA', 'WMA', 'DMA',
    'MACD', 'KDJ', 'RSI', 'WR', 'BIAS', 'BOLL', 'PSY', 'CCI', 'ATR', 'BBI', 'DMI',
    'HHV', 'LLV', 'REF', 'STD', 'SUM', 'CROSS',
]
