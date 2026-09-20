"""Fail-closed restart admission. Does not infer or submit missing executions."""
from datetime import datetime
import math


class RestartBlocked(RuntimeError):
    pass


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or not {'positions','orders','trades','cash'} <= snapshot.keys():
        raise RestartBlocked('RESTART-BLOCKED: incomplete broker snapshot')
    if not isinstance(snapshot['cash'], (float, int)) or not math.isfinite(snapshot['cash']):
        raise RestartBlocked('RESTART-BLOCKED: invalid cash')
    for field in ('positions','orders','trades'):
        if not isinstance(snapshot[field], list):
            raise RestartBlocked('RESTART-BLOCKED: invalid '+field)
    codes = set()
    for row in snapshot['positions']:
        if len(row) != 3 or row[0] in codes or not 0 <= row[2] <= row[1]:
            raise RestartBlocked('RESTART-BLOCKED: invalid position')
        codes.add(row[0])
    for row in snapshot['orders']:
        if len(row) != 5 or row[2] not in (53,54,56,57):
            raise RestartBlocked('RESTART-BLOCKED: pending/unknown broker order')
    if len({row[0] for row in snapshot['trades']}) != len(snapshot['trades']):
        raise RestartBlocked('RESTART-BLOCKED: duplicate trade identity')


def validate_restart(saved, current, today):
    """Return SAME_DAY or NEW_DAY only for a demonstrably unchanged account.

    Missing checkpoint is not a fresh-account proof. Bootstrap is deliberately
    outside automatic recovery. Broker-side changes require human reconciliation.
    """
    validate_snapshot(current)
    if not saved:
        raise RestartBlocked('RESTART-BLOCKED: missing v57 checkpoint; explicit baseline enrollment required')
    if saved.get('strategy') != 'v57_restart_guard':
        raise RestartBlocked('RESTART-BLOCKED: foreign/legacy checkpoint; migration not automatic')
    if saved.get('inflight') or saved.get('order_uncertain'):
        raise RestartBlocked('RESTART-BLOCKED: uncertain order retained; no automatic resend')
    previous = saved.get('broker')
    validate_snapshot(previous)
    try:
        prior_day = datetime.strptime(saved['date'], '%Y%m%d').date()
        now_day = datetime.strptime(today, '%Y%m%d').date()
    except (KeyError, TypeError, ValueError) as error:
        raise RestartBlocked('RESTART-BLOCKED: invalid checkpoint date') from error
    if prior_day > now_day:
        raise RestartBlocked('RESTART-BLOCKED: future checkpoint')
    if abs(current['cash']-previous['cash']) > .01:
        raise RestartBlocked('RESTART-BLOCKED: cash changed; reconcile dividends/transfers/fills')
    inventory = lambda state: sorted((r[0],r[1]) for r in state['positions'])
    if inventory(current) != inventory(previous):
        raise RestartBlocked('RESTART-BLOCKED: inventory changed')
    if prior_day == now_day:
        if current != previous:
            raise RestartBlocked('RESTART-BLOCKED: intraday orders/trades/sellable changed')
        return 'SAME_DAY'
    for field in ('orders','trades'):
        if current[field] and current[field] != previous[field]:
            raise RestartBlocked('RESTART-BLOCKED: new-day unaccounted '+field)
    old = {r[0]:r[2] for r in previous['positions']}
    if any(r[2] < old[r[0]] for r in current['positions']):
        raise RestartBlocked('RESTART-BLOCKED: new-day sellable decreased')
    return 'NEW_DAY'
