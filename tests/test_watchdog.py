"""firmware/watchdog.py: a timer-gated hardware watchdog with check-ins."""

import sys

import pytest

from firmware import watchdog


class FakeWdt:
    instances = []

    def __init__(self, timeout):
        self.timeout = timeout
        self.feeds = 0
        FakeWdt.instances.append(self)

    def feed(self):
        self.feeds += 1


class FakeTimer:
    PERIODIC = "periodic"
    instances = []

    def __init__(self, period, mode, callback):
        self.period = period
        self.mode = mode
        self.callback = callback
        self.stopped = False
        FakeTimer.instances.append(self)

    def deinit(self):
        self.stopped = True


class Clock:
    def __init__(self):
        self.ms = 1000

    def __call__(self):
        return self.ms

    def advance(self, seconds):
        self.ms += int(seconds * 1000)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    watchdog._forget()
    FakeWdt.instances = []
    FakeTimer.instances = []
    clock = Clock()
    monkeypatch.setattr(watchdog, "now_ms", clock)
    yield clock
    watchdog._forget()


def start(**kwargs):
    kwargs.setdefault("marker_exists", lambda: False)
    return watchdog.start(wdt=FakeWdt, timer=FakeTimer, **kwargs)


def test_start_arms_the_watchdog_and_a_one_second_timer(capsys):
    assert start() is True
    (wdt,) = FakeWdt.instances
    (timer,) = FakeTimer.instances
    assert wdt.timeout == watchdog.HW_TIMEOUT_MS <= 8388  # the hardware maximum
    assert timer.period == watchdog.TICK_MS == 1000
    assert timer.mode == FakeTimer.PERIODIC
    assert "armed" in capsys.readouterr().out


def test_start_twice_arms_only_once():
    start()
    assert start() is True
    assert len(FakeWdt.instances) == len(FakeTimer.instances) == 1


def test_the_marker_file_keeps_the_watchdog_off(capsys):
    assert start(marker_exists=lambda: True) is False
    assert FakeWdt.instances == [] and FakeTimer.instances == []
    assert "/no_wdt" in capsys.readouterr().out


def test_on_the_host_there_is_nothing_to_arm(monkeypatch):
    monkeypatch.setitem(sys.modules, "machine", None)  # import machine -> ImportError
    assert watchdog.start(marker_exists=lambda: False) is False


def test_the_timer_feeds_while_the_program_checks_in(fresh):
    start()
    timer, wdt = FakeTimer.instances[0], FakeWdt.instances[0]
    for _ in range(100):
        fresh.advance(1)
        watchdog.checkin()
        timer.callback(timer)
    assert wdt.feeds == 100


def test_the_timer_stops_feeding_once_the_budget_is_used_up(fresh, capsys):
    start(budget_ms=60000)
    timer, wdt = FakeTimer.instances[0], FakeWdt.instances[0]
    fresh.advance(59)
    timer.callback(timer)
    assert wdt.feeds == 1  # still inside the budget
    fresh.advance(2)
    timer.callback(timer)
    timer.callback(timer)
    assert wdt.feeds == 1  # starved: the hardware watchdog will reset the board
    out = capsys.readouterr().out
    assert out.count("no check-in") == 1  # warned once, not every second


def test_feeding_resumes_after_a_late_check_in(fresh, capsys):
    start(budget_ms=60000)
    timer, wdt = FakeTimer.instances[0], FakeWdt.instances[0]
    fresh.advance(120)
    timer.callback(timer)
    watchdog.checkin()
    timer.callback(timer)
    assert wdt.feeds == 1
    fresh.advance(120)
    timer.callback(timer)
    assert capsys.readouterr().out.count("no check-in") == 2  # a second stall warns again


def test_nap_sleeps_in_one_second_slices_and_checks_in_each_time(fresh):
    sleeps = []
    checkins = []
    real = watchdog.checkin
    watchdog.checkin = lambda: (checkins.append(1), real())
    try:
        watchdog.nap(2.5, sleep=sleeps.append)
    finally:
        watchdog.checkin = real
    assert sleeps == [1, 1, 0.5]
    assert len(checkins) == 4  # once up front and after every slice


@pytest.mark.parametrize("seconds", [0, -3])
def test_nap_of_nothing_does_not_sleep(seconds):
    sleeps = []
    watchdog.nap(seconds, sleep=sleeps.append)
    assert sleeps == []


def test_nap_keeps_a_long_wait_inside_the_budget(fresh):
    start(budget_ms=60000)
    timer, wdt = FakeTimer.instances[0], FakeWdt.instances[0]

    def sleep(seconds):
        fresh.advance(seconds)
        timer.callback(timer)

    watchdog.nap(600, sleep=sleep)  # ten minutes of waiting
    assert wdt.feeds == 600


def test_everything_is_harmless_before_start():
    watchdog.checkin()
    watchdog.nap(1, sleep=lambda s: None)
    watchdog._tick()
