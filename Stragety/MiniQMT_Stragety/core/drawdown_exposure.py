"""Pure per-symbol drawdown exposure policy for research strategies."""

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class DrawdownPolicy:
    name: str
    levels: tuple
    confirmation_minutes: int = 3
    restore_step: float = 0.25
    restore_closes: int = 2


@dataclass(frozen=True)
class ExposureDecision:
    action: str
    target_fraction: float
    reason: str


def aggressive_policy():
    return DrawdownPolicy('AGGRESSIVE_LIQUIDATION', ((0.10, 0.0),))


def staged_policy():
    return DrawdownPolicy('STAGED_DERISK', (
        (0.08, 0.75),
        (0.12, 0.50),
        (0.16, 0.25),
        (0.20, 0.0),
    ))


class DrawdownExposureController:
    """Convert completed closes and minute prices into target exposure changes."""

    def __init__(self, policy):
        self.policy = policy
        self.peak_close = 0.0
        self.trough_price = 0.0
        self.target_fraction = 1.0
        self.confirmation_count = 0
        self.pending_fraction = 1.0
        self.last_minute = ''
        self.minute_day = ''
        self.minute_value = None
        self.minute_price = 0.0
        self.minute_ma20 = 0.0
        self.restore_streak = 0
        self.restore_ready_day = ''
        self.last_restore_day = ''
        self.latest_close = 0.0
        self.mode = 'NORMAL'
        self.recovery_anchor_set = False
        self.derisk_events = 0
        self.restore_events = 0

    @staticmethod
    def _valid_positive(value):
        return math.isfinite(float(value)) and float(value) > 0

    def _desired_fraction(self, drawdown):
        desired = 1.0
        for threshold, fraction in self.policy.levels:
            if drawdown + 1e-12 >= threshold:
                desired = min(desired, fraction)
        return desired

    def observe_close(self, day, close, ma20, previous_ma20):
        if not all(self._valid_positive(value)
                   for value in (close, ma20, previous_ma20)):
            self.restore_streak = 0
            self.restore_ready_day = ''
            return
        close = float(close)
        ma20 = float(ma20)
        previous_ma20 = float(previous_ma20)
        self.latest_close = close
        if self.target_fraction >= 1.0 or self.mode == 'RECOVERING':
            self.peak_close = max(self.peak_close, close)
        elif self.peak_close <= 0:
            self.peak_close = close
        self.trough_price = close if self.trough_price <= 0 else min(
            self.trough_price, close)
        if self.target_fraction < 1.0 and close > ma20 and ma20 > previous_ma20:
            self.restore_streak += 1
            if self.restore_streak >= self.policy.restore_closes:
                self.restore_ready_day = str(day)
        else:
            self.restore_streak = 0
            self.restore_ready_day = ''

    def observe_minute(self, day, minute, price, ma20):
        if not all(self._valid_positive(value) for value in (price, ma20)):
            self._reset_confirmation()
            return None
        price = float(price)
        ma20 = float(ma20)
        if self.peak_close <= 0:
            self._reset_confirmation()
            return None
        self.trough_price = price if self.trough_price <= 0 else min(
            self.trough_price, price)
        minute_key = '{}:{}'.format(day, minute)
        if minute_key == self.last_minute:
            self.minute_price = price
            self.minute_ma20 = ma20
            return None
        decision = None
        consecutive = (
            self.last_minute and self.minute_day == str(day) and
            self.minute_value is not None and
            int(minute) == int(self.minute_value) + 1)
        if consecutive:
            decision = self._observe_completed_minute(
                self.minute_price, self.minute_ma20)
        elif self.last_minute:
            self._reset_confirmation()
        self.last_minute = minute_key
        self.minute_day = str(day)
        self.minute_value = int(minute)
        self.minute_price = price
        self.minute_ma20 = ma20
        if decision is not None and decision.action == 'DERISK':
            self.trough_price = price
        return decision

    def _observe_completed_minute(self, price, ma20):
        drawdown = max(0.0, 1.0 - float(price) / self.peak_close)
        desired = self._desired_fraction(drawdown)
        if price >= ma20 or desired >= self.target_fraction:
            self._reset_confirmation()
            return None
        if desired != self.pending_fraction:
            self.pending_fraction = desired
            self.confirmation_count = 1
        else:
            self.confirmation_count += 1
        if self.confirmation_count < self.policy.confirmation_minutes:
            return None
        self.target_fraction = desired
        self.mode = 'DERISKED'
        self.restore_streak = 0
        self.restore_ready_day = ''
        self.recovery_anchor_set = False
        self.trough_price = float(price)
        self.derisk_events += 1
        self._reset_confirmation()
        return ExposureDecision(
            'DERISK', desired,
            '{} drawdown {:.2f}% below MA20'.format(
                self.policy.name, drawdown * 100))

    def reentry_decision(self, day, now_hms):
        day = str(day)
        if (self.target_fraction >= 1.0 or not self.restore_ready_day or
                day <= self.restore_ready_day or now_hms < '09:35:00' or
                self.last_restore_day == day):
            return None
        target = min(1.0, self.target_fraction + self.policy.restore_step)
        self.target_fraction = target
        self.restore_events += 1
        self.last_restore_day = day
        self.mode = 'NORMAL' if target >= 1.0 else 'RECOVERING'
        if not self.recovery_anchor_set:
            self.peak_close = self.latest_close
            self.trough_price = self.latest_close
            self.recovery_anchor_set = True
        if target >= 1.0:
            self.restore_streak = 0
            self.restore_ready_day = ''
            self.recovery_anchor_set = False
        return ExposureDecision(
            'RESTORE', target,
            'two closes above rising MA20; restore 25%')

    def _reset_confirmation(self):
        self.confirmation_count = 0
        self.pending_fraction = self.target_fraction

    def record(self):
        data = dict(self.__dict__)
        data.pop('policy')
        data['policy'] = asdict(self.policy)
        return data

    @classmethod
    def from_record(cls, policy, record):
        saved_policy = record.get('policy', {})
        if saved_policy.get('name') != policy.name:
            raise ValueError('drawdown policy differs from checkpoint')
        controller = cls(policy)
        for key in controller.__dict__:
            if key != 'policy' and key in record:
                setattr(controller, key, record[key])
        return controller
