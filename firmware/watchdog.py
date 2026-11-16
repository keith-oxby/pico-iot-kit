"""Hardware watchdog with a software check-in gate (Sprint 4, item 4e).

The RP2040 watchdog can't be stopped once started and times out after at most
8.388 s, which is shorter than waits the firmware makes on purpose (Wi-Fi join,
NTP, a blocking connect, the pause between readings). So a 1 s timer feeds it,
but only while the program has called checkin() within the last BUDGET_MS:

- the main loop stalls (nothing checks in): the timer prints one warning and
  stops feeding, and the watchdog resets the board;
- the whole chip locks up (the timer can't run): the hardware trips on its own
  within HW_TIMEOUT_MS.

Measured on a Pico W: the timer keeps running through time.sleep(), a blocking
socket connect and a blocking DNS lookup, so slow but healthy calls are safe.
A soft reset stops the timer but not the watchdog, so a board running this
resets about 8 s after an mpremote session starts; create /no_wdt to keep the
watchdog off while developing:  mpremote exec "open('/no_wdt','w').close()"
and remove it with:  mpremote rm :no_wdt

machine.reset() on the RP2 port goes through the watchdog, so reset_cause()
can't tell a watchdog trip from a deliberate reset; read the log lines instead.

Neutral module (imports only `time`): provisioning and the runtime both call
checkin()/nap() without depending on each other. Before start(), and on the
host, everything here is a harmless no-op.
"""

import time

HW_TIMEOUT_MS = 8000  # the hardware maximum is 8388
TICK_MS = 1000
BUDGET_MS = 60000
NO_WDT_PATH = "/no_wdt"

if hasattr(time, "ticks_ms"):  # MicroPython
    now_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:  # CPython (host tests)

    def now_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


_wdt = None
_timer = None
_last = 0
_budget = BUDGET_MS
_warned = False


def _marker_exists():
    import os

    try:
        os.stat(NO_WDT_PATH)
    except OSError:
        return False
    return True


def start(wdt=None, timer=None, marker_exists=None, budget_ms=BUDGET_MS):
    """Arm the watchdog. Returns True if it is running.

    Does nothing on the host, when already started, or when /no_wdt exists.
    The arguments are for tests; by default `machine.WDT` and `machine.Timer`.
    """
    global _wdt, _timer, _last, _budget, _warned
    if _wdt is not None:
        return True
    if (marker_exists or _marker_exists)():
        print("watchdog: off (%s exists)" % NO_WDT_PATH)
        return False
    if wdt is None:
        try:
            import machine
        except ImportError:
            return False
        wdt, timer = machine.WDT, machine.Timer
    _budget = budget_ms
    _warned = False
    _last = now_ms()
    _wdt = wdt(timeout=HW_TIMEOUT_MS)
    _timer = timer(period=TICK_MS, mode=timer.PERIODIC, callback=_tick)
    print("watchdog: armed (%d s check-in budget)" % (budget_ms // 1000))
    return True


def checkin():
    """Tell the watchdog the program is still making progress."""
    global _last
    _last = now_ms()


def nap(seconds, sleep=time.sleep):
    """Sleep `seconds`, checking in every second, so a long wait never looks
    like a stall."""
    checkin()
    remaining = seconds
    while remaining > 0:
        step = 1 if remaining > 1 else remaining
        sleep(step)
        remaining -= step
        checkin()


def _tick(_=None):
    """Timer callback: feed the hardware watchdog while the program is alive."""
    global _warned
    if _wdt is None:
        return
    if ticks_diff(now_ms(), _last) < _budget:
        _wdt.feed()
        _warned = False
    elif not _warned:
        _warned = True
        print("watchdog: no check-in for %d s, letting the watchdog reset the device" % (_budget // 1000))


def _forget():
    """Drop the module state (tests only; a real watchdog can't be stopped)."""
    global _wdt, _timer, _warned
    if _timer is not None:
        _timer.deinit()
    _wdt = None
    _timer = None
    _warned = False
