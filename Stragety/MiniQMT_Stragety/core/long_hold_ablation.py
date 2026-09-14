"""Independent factorial policy using shared indicators, not sibling strategies."""
from dataclasses import dataclass
from .long_hold_allocation import calculate_indicators, classify_regime
from .long_hold_allocation_v2 import confirmed_regime, risk_state


@dataclass
class AblationPolicy:
    full_bull: bool = False
    remove_drawdown: bool = False
    remove_weakness: bool = False
    active: str | None = None
    candidate: str | None = None
    streak: int = 0
    swing: float = 0.

    def decide(self, frame, weight, drawdown, signal_date, execution_date):
        ind = calculate_indicators(frame.high.tolist(),frame.low.tolist(),frame.close.tolist())
        candidate = classify_regime(ind)[0]
        self.streak = self.streak+1 if candidate == self.candidate else 1
        self.candidate = candidate
        self.active = confirmed_regime(candidate,self.active,self.streak)
        target = {'STRONG_BULL':1.,'BULL':1. if self.full_bull else .85,'SIDEWAYS':.6,'BEAR':.3}[self.active]
        reasons = ['REGIME_'+self.active]
        if candidate != self.active:
            reasons.append('REGIME_CONFIRMATION_PENDING')
        if not self.remove_weakness and self.active in ('STRONG_BULL','BULL') and ind['close']<ind['ma60'] and ind['signed_efficiency20']<0:
            target = min(target,.6)
            reasons.append('CONFIRMED_WEAKNESS_CAP_60')
        if ind['close']<ind['ma120'] and ind['ma60_slope']<0:
            target = .3
            reasons.append('LONG_TREND_BROKEN_30')
        state = 'NORMAL' if self.remove_drawdown else risk_state(drawdown)
        for name, cap, reason in [('REDUCE_ONE_TIER',.75,'DRAWDOWN_15_CAP_75'),('DEFENSIVE_30',.45,'DRAWDOWN_20_CAP_45'),('HALT_BUYS',.3,'DRAWDOWN_25_HALT_BUYS')]:
            if state == name:
                target = min(target,cap)
                reasons.append(reason)
        return dict(regime=self.active,current_weight=round(weight,6),target_weight=round(target,6),
                    risk_state=state,risk_order=target<=.45,reason_codes=reasons,indicators=ind,
                    signal_date=signal_date,execution_date=execution_date)
