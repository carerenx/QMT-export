"""Daily decisions for the four registered research experiments.

Only completed daily observations enter this module. Execution owns inventory.
"""
from dataclasses import dataclass

from .long_hold_allocation import calculate_indicators, classify_regime
from .long_hold_allocation_v2 import confirmed_regime, decide_allocation


EXPERIMENTS = ('v2_control', 'profit_core', 'split_execution', 'swing_fusion')


@dataclass
class Policy:
    experiment: str
    confirmation: int = 3
    atr_threshold: float = 2.0
    active: str | None = None
    candidate: str | None = None
    streak: int = 0
    swing: float = 0.0
    high: float | None = None
    breakout: int = 0

    def decide(self, frame, weight, drawdown, signal_date, execution_date):
        indicators = calculate_indicators(frame.high.tolist(), frame.low.tolist(), frame.close.tolist())
        candidate = classify_regime(indicators)[0]
        self.streak = self.streak + 1 if candidate == self.candidate else 1
        self.candidate = candidate
        if self.experiment == 'v2_control':
            result = decide_allocation(frame.high.tolist(), frame.low.tolist(), frame.close.tolist(),
                                      weight, drawdown, signal_date, execution_date,
                                      self.active, self.streak)
            self.active = result['regime']
            result['risk_order'] = result['target_weight'] <= .45
            return result
        self.active = confirmed_regime(candidate, self.active, self.streak, self.confirmation)
        base = {'STRONG_BULL': 1., 'BULL': 1., 'SIDEWAYS': .6, 'BEAR': .3}[self.active]
        reasons = ['REGIME_' + self.active]
        risk = indicators['close'] < indicators['ma120'] and indicators['ma60_slope'] < 0
        if risk:
            base = .3
            self.swing = 0.
            self.high = None
            reasons.append('TREND_RISK_EXIT')
        elif self.experiment == 'swing_fusion':
            close = indicators['close']
            if self.swing:
                # Compare BEFORE including today's close in the running maximum.
                self.breakout = self.breakout + 1 if close > self.high else 0
                recovered = close <= indicators['ma20'] and close >= indicators['previous_close']
                if recovered or self.breakout >= 2:
                    self.swing = 0.
                    reasons.append('SWING_RESTORE')
                self.high = max(self.high, close)
            elif (close - indicators['ma20'] >= self.atr_threshold * indicators['atr20']
                  and frame.close.iloc[-1] < frame.close.iloc[-2] < frame.close.iloc[-3]):
                self.swing = .2 if self.active in ('BULL', 'STRONG_BULL') else .3
                self.high = close
                self.breakout = 0
                reasons.append('SWING_REDUCE')
        return dict(regime=self.active, current_weight=weight,
                    target_weight=max(.3, base - self.swing), risk_state='NORMAL',
                    reason_codes=reasons, risk_order=risk, indicators=indicators,
                    signal_date=signal_date, execution_date=execution_date)
