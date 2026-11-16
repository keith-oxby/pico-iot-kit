import sys

import pytest

from firmware.runtime import clock


class FakeTime:
    def __init__(self, year=2021):
        self.year = year

    def localtime(self):
        return (self.year, 1, 1, 0, 0, 0, 0, 1)


class FakeNtp:
    """settime() fails `failures` times, then sets the fake clock."""

    def __init__(self, fake_time, failures=0, error=None, sets_clock=True):
        self.fake_time = fake_time
        self.failures = failures
        self.error = error or OSError(110)
        self.sets_clock = sets_clock
        self.calls = 0

    def settime(self):
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise self.error
        if self.sets_clock:
            self.fake_time.year = 2026


@pytest.fixture
def rig(monkeypatch):
    fake_time = FakeTime()
    monkeypatch.setattr(clock.time, "localtime", fake_time.localtime)

    def install(**kwargs):
        ntp = FakeNtp(fake_time, **kwargs)
        monkeypatch.setitem(sys.modules, "ntptime", ntp)
        return ntp

    rig = type("Rig", (), {})()
    rig.time = fake_time
    rig.install = install
    rig.sleeps = []
    return rig


@pytest.mark.parametrize("year,expected", [(2021, False), (2024, False), (2025, True), (2026, True)])
def test_is_set_knows_a_never_set_rtc(rig, year, expected):
    rig.time.year = year
    assert clock.is_set() is expected


def test_an_already_set_clock_is_left_alone(rig):
    rig.time.year = 2026
    ntp = rig.install()
    clock.sync(sleep=rig.sleeps.append)
    assert ntp.calls == 0 and rig.sleeps == []


def test_sync_sets_the_clock(rig):
    ntp = rig.install()
    clock.sync(sleep=rig.sleeps.append)
    assert clock.is_set() and ntp.calls == 1 and rig.sleeps == []


def test_sync_retries_with_backoff_then_succeeds(rig):
    ntp = rig.install(failures=3)
    clock.sync(sleep=rig.sleeps.append)
    assert clock.is_set() and ntp.calls == 4
    assert rig.sleeps == [1, 2, 4]


def test_backoff_is_capped(rig, monkeypatch):
    monkeypatch.setattr(clock, "MAX_ATTEMPTS", 8)
    rig.install(failures=7)
    clock.sync(sleep=rig.sleeps.append)
    assert rig.sleeps == [1, 2, 4, 8, 8, 8, 8]


def test_giving_up_raises_an_oserror_after_the_attempts(rig):
    ntp = rig.install(failures=99)
    with pytest.raises(OSError, match="NTP"):
        clock.sync(sleep=rig.sleeps.append)
    assert ntp.calls == clock.MAX_ATTEMPTS
    assert len(rig.sleeps) == clock.MAX_ATTEMPTS - 1  # no pointless sleep after the last try
    assert not clock.is_set()


def test_a_reply_that_leaves_the_clock_wrong_counts_as_a_failure(rig):
    ntp = rig.install(sets_clock=False)
    with pytest.raises(OSError):
        clock.sync(sleep=rig.sleeps.append)
    assert ntp.calls == clock.MAX_ATTEMPTS


def test_unexpected_ntp_errors_are_retried_too(rig):
    rig.install(failures=1, error=ValueError("bad reply"))
    clock.sync(sleep=rig.sleeps.append)
    assert clock.is_set()
