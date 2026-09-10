"""Independent observability for synchronous SDK calls; never repeats orders."""
import threading
import time
from contextlib import contextmanager


class RuntimeWatchdog:
    def __init__(self, log, threshold=15.0, clock=time.monotonic):
        self.log, self.threshold, self.clock = log, threshold, clock
        self.active = ()
        self.stop_event = threading.Event()
        self.thread = None

    @contextmanager
    def scope(self, label):
        previous = self.active
        started = self.clock()
        self.active = (label, started)
        try:
            yield
        finally:
            elapsed = self.clock() - started
            if elapsed >= self.threshold:
                self.log('[CALL-SLOW] {} elapsed={:.1f}s'.format(label, elapsed))
            self.active = previous

    def check(self):
        active = self.active
        if active and self.clock() - active[1] >= self.threshold:
            self.log('[WATCHDOG-STALL] {} elapsed={:.1f}s'.format(
                active[0], self.clock() - active[1]))
            return True
        return False

    def start(self):
        def monitor():
            while not self.stop_event.wait(self.threshold):
                self.check()
        self.thread = threading.Thread(target=monitor, name='dayt-watchdog', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)


def instrument_rpc(client, watchdog, read_timeout):
    """BigQMT local client supports per-request timeout_seconds (not SDK-wide)."""
    original = client.call

    def call(method, params=None, account_id=None, timeout_seconds=None):
        if method in ('get_full_tick', 'query_stock_asset', 'query_stock_positions',
                      'query_stock_orders', 'query_stock_trades', 'query_account_infos'):
            timeout_seconds = min(float(timeout_seconds or read_timeout), read_timeout)
        with watchdog.scope('RPC ' + method):
            return original(method, params, account_id=account_id,
                            timeout_seconds=timeout_seconds)
    client.call = call
    return original
