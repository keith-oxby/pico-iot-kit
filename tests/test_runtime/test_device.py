import pytest

from firmware.runtime import device as device_module
from firmware.runtime.device import (
    BACKOFF_MAX_S,
    DEFAULT_POLL_INTERVAL_S,
    Device,
)


class FakeSensor:
    def __init__(self, name, reading=None, fail=False):
        self.name = name
        self.topic = name
        self.reading = reading if reading is not None else {"value": 1}
        self.fail = fail

    def read(self):
        if self.fail:
            raise OSError("sensor not responding")
        return self.reading


class FakeAdapter:
    def __init__(self, log):
        self.log = log
        self.published = []
        self.commands = []
        self.connect_failures = 0
        self.publish_failures = 0
        self.poll_failures = 0
        self.config = None

    def connect(self):
        self.log.append("connect")
        if self.connect_failures:
            self.connect_failures -= 1
            raise OSError("broker unreachable")

    def announce(self, sensors):
        self.log.append("announce")

    def publish(self, topic, reading):
        if self.publish_failures:
            self.publish_failures -= 1
            raise OSError("socket closed")
        self.published.append((topic, reading))

    def poll_commands(self):
        if self.poll_failures:
            self.poll_failures -= 1
            raise OSError("socket closed")
        return self.commands.pop(0) if self.commands else None


CONFIG = {
    "wifi": {"ssid": "home", "password": "hunter2hunter2"},
    "backend": {"type": "local_mqtt", "host": "broker.local"},
    "sensors": [{"type": "dht11", "name": "a", "pin": 4}],
}


@pytest.fixture
def rig(monkeypatch, fake_hardware):
    """A Device wired to fakes; `rig.log` records ordered events."""

    class Rig:
        pass

    r = Rig()
    r.log = []
    r.sleeps = []
    r.sensors = [FakeSensor("a"), FakeSensor("b", {"temperature_c": 20.5})]
    r.adapter = FakeAdapter(r.log)
    r.wifi_failures = 0
    fake_hardware["machine"].unique_id.return_value = b"\xe6\x61\x64\x12\xab\xcd\xef\x01"

    def fake_wifi_connect(ssid, password):
        r.log.append(("wifi", ssid, password))
        if r.wifi_failures:
            r.wifi_failures -= 1
            raise device_module.wifi.WifiError("no AP")

    def fake_load_adapter(backend_config):
        r.adapter.config = backend_config
        return r.adapter

    monkeypatch.setattr(device_module.wifi, "connect", fake_wifi_connect)
    monkeypatch.setattr(device_module, "load_sensor_adapters", lambda cfg: r.sensors)
    monkeypatch.setattr(device_module, "load_downstream_adapter", fake_load_adapter)
    r.make = lambda config=CONFIG: Device(config, sleep=r.sleeps.append)
    return r


def test_startup_order_is_wifi_connect_announce(rig):
    rig.make().run(max_cycles=0)
    assert rig.log == [("wifi", "home", "hunter2hunter2"), "connect", "announce"]


def test_cycle_publishes_every_sensor_then_sleeps(rig):
    rig.make().run(max_cycles=2)
    assert rig.adapter.published == [
        ("a", {"value": 1}),
        ("b", {"temperature_c": 20.5}),
    ] * 2
    assert rig.sleeps == [DEFAULT_POLL_INTERVAL_S] * 2


def test_poll_interval_comes_from_config(rig):
    rig.make(dict(CONFIG, poll_interval_s=5)).run(max_cycles=1)
    assert rig.sleeps == [5]


def test_failing_sensor_is_skipped_and_others_still_publish(rig):
    rig.sensors[0].fail = True
    rig.make().run(max_cycles=2)
    assert rig.adapter.published == [("b", {"temperature_c": 20.5})] * 2
    assert rig.log.count("connect") == 1  # no reconnect for a sensor fault


def test_sensor_recovers_on_a_later_cycle(rig):
    sensor = rig.sensors[0]
    reads = iter([True, False])
    real_read = sensor.read

    def flaky():
        sensor.fail = next(reads)
        return real_read()

    sensor.read = flaky
    rig.make().run(max_cycles=2)
    assert ("a", {"value": 1}) in rig.adapter.published


def test_publish_failure_reconnects_and_reannounces(rig):
    rig.adapter.publish_failures = 1
    rig.make().run(max_cycles=2)
    assert rig.log.count("connect") == 2
    assert rig.log.count("announce") == 2
    assert [e for e in rig.log if isinstance(e, tuple)] == [("wifi", "home", "hunter2hunter2")] * 2
    assert rig.adapter.published  # the next cycle publishes again


def test_poll_commands_failure_reconnects(rig):
    rig.adapter.poll_failures = 1
    rig.make().run(max_cycles=2)
    assert rig.log.count("connect") == 2


def test_startup_connect_retries_with_exponential_backoff(rig):
    rig.adapter.connect_failures = 3
    rig.wifi_failures = 0
    rig.make().run(max_cycles=0)
    assert rig.sleeps == [1, 2, 4]
    assert rig.log.count("announce") == 1


def test_wifi_failure_is_retried_too(rig):
    rig.wifi_failures = 2
    rig.make().run(max_cycles=0)
    assert rig.sleeps == [1, 2]
    assert rig.log.count("connect") == 1


def test_backoff_is_capped(rig):
    rig.adapter.connect_failures = 9
    rig.make().run(max_cycles=0)
    assert rig.sleeps == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert max(rig.sleeps) == BACKOFF_MAX_S


def test_backoff_resets_after_a_successful_connect(rig):
    d = rig.make()
    rig.adapter.connect_failures = 2
    d.run(max_cycles=0)
    assert rig.sleeps == [1, 2]
    rig.sleeps.clear()
    rig.adapter.connect_failures = 1
    rig.adapter.publish_failures = 1
    d.run(max_cycles=1)
    assert rig.sleeps[0] == 1  # not 4


def test_command_is_dispatched(rig, monkeypatch):
    seen = []
    monkeypatch.setattr(Device, "_dispatch", lambda self, cmd: seen.append(cmd))
    rig.adapter.commands = [{"do": "x"}]
    rig.make().run(max_cycles=2)
    assert seen == [{"do": "x"}]


def test_dispatch_error_neither_reconnects_nor_stops_the_loop(rig, monkeypatch):
    def boom(self, cmd):
        raise ValueError("bad command")

    monkeypatch.setattr(Device, "_dispatch", boom)
    rig.adapter.commands = [{"do": "x"}]
    rig.make().run(max_cycles=2)
    assert rig.log.count("connect") == 1
    assert len(rig.adapter.published) == 4


def test_device_id_is_injected_from_hardware(rig):
    rig.make()
    assert rig.adapter.config["device_id"] == "e6616412abcdef01"
    assert rig.adapter.config["host"] == "broker.local"


def test_configured_device_id_cannot_override_hardware_id(rig):
    config = dict(CONFIG, backend=dict(CONFIG["backend"], device_id="evil/#"))
    rig.make(config)
    assert rig.adapter.config["device_id"] == "e6616412abcdef01"


def test_config_dict_is_not_mutated(rig):
    rig.make()
    assert "device_id" not in CONFIG["backend"]


def test_ctrl_c_is_not_swallowed(rig):
    def interrupt(*a):
        raise KeyboardInterrupt

    rig.adapter.publish = interrupt
    with pytest.raises(KeyboardInterrupt):
        rig.make().run(max_cycles=1)


def test_the_poll_interval_reaches_the_backend_for_its_keepalive(rig):
    rig.make(dict(CONFIG, poll_interval_s=45))
    assert rig.adapter.config["poll_interval_s"] == 45


def test_the_default_poll_interval_reaches_the_backend_too(rig):
    rig.make()
    assert rig.adapter.config["poll_interval_s"] == DEFAULT_POLL_INTERVAL_S


# --- the stall reset ---------------------------------------------------------

class StallRig:
    """A Device on a fake clock that sleeps by advancing it."""

    def __init__(self, rig):
        self.rig = rig
        self.ms = 0
        self.resets = []

    def now(self):
        return self.ms

    def sleep(self, seconds):
        self.ms += int(seconds * 1000)

    def reset(self):
        self.resets.append(self.ms // 1000)
        raise SystemExit  # a real reset never returns

    def device(self):
        return Device(CONFIG, sleep=self.sleep, reset=self.reset, now_ms=self.now)


@pytest.fixture
def stall(rig):
    return StallRig(rig)


def test_the_device_resets_after_fifteen_minutes_without_a_connection(rig, stall):
    rig.adapter.connect_failures = 10_000
    with pytest.raises(SystemExit):
        stall.device().run(max_cycles=0)
    (when,) = stall.resets
    assert device_module.STALL_RESET_S == 15 * 60
    assert 900 <= when < 900 + device_module.BACKOFF_MAX_S  # checked on every retry


def test_the_stall_reset_is_logged(rig, stall, capsys):
    rig.adapter.connect_failures = 10_000
    with pytest.raises(SystemExit):
        stall.device().run(max_cycles=0)
    assert "no connection for 15 minutes, restarting" in capsys.readouterr().out


def test_the_log_line_gets_a_moment_to_leave_before_the_reset(rig, stall, capsys):
    order = []
    d = stall.device()
    d._sleep, real_sleep = (lambda s: (order.append("sleep"), real_sleep(s))[1]), d._sleep
    d._reset = lambda: (order.append("reset"), stall.reset())[1]
    rig.adapter.connect_failures = 10_000
    with pytest.raises(SystemExit):
        d.run(max_cycles=0)
    assert order[-2:] == ["sleep", "reset"]  # the last thing before the reset is a pause


def test_a_shorter_outage_does_not_reset(rig, stall):
    rig.adapter.connect_failures = 8  # 1+2+4+8+16+32+60+60 s of waiting: about 3 minutes
    stall.device().run(max_cycles=0)
    assert stall.resets == []


def test_a_success_restarts_the_stall_clock(rig, stall):
    d = stall.device()
    for _ in range(2):
        rig.adapter.connect_failures = 12  # about 10 minutes of failures each time
        d.run(max_cycles=0)
        assert stall.ms < 3 * 3600 * 1000
    assert stall.resets == []  # 20 minutes in all, but never 15 without a success


def test_publishing_normally_for_hours_never_resets(rig, stall):
    stall.device().run(max_cycles=400)  # 400 cycles of 30 s: more than three hours
    assert stall.resets == []


def test_one_failed_reconnect_after_hours_of_normal_running_does_not_reset(rig, stall):
    d = stall.device()
    d.run(max_cycles=300)  # 2.5 hours of healthy publishing
    rig.adapter.publish_failures = 1
    rig.adapter.connect_failures = 1  # the reconnect fails once, then works
    d.run(max_cycles=2)
    assert stall.resets == []  # the last success was seconds ago, not hours


def test_a_connection_that_works_but_loses_every_reading_is_not_a_stall(rig, stall):
    rig.adapter.publish_failures = 10_000
    rig.adapter.connect_failures = 0
    d = stall.device()
    # each cycle: publish fails, the reconnect succeeds: the device is
    # connecting fine, so it is not stalled, however many readings are lost
    d.run(max_cycles=200)
    assert stall.resets == []


def test_the_default_sleep_checks_in_with_the_watchdog():
    from firmware import watchdog

    assert Device.__init__.__defaults__[0] is watchdog.nap


def test_free_heap_is_logged_every_twenty_cycles(rig, monkeypatch, capsys):
    import gc

    monkeypatch.setattr(gc, "mem_free", lambda: 123456, raising=False)
    rig.make().run(max_cycles=41)
    out = capsys.readouterr().out
    assert out.count("device: heap free 123456 bytes") == 3  # cycles 0, 20 and 40


def test_no_heap_line_on_a_host_without_mem_free(rig, capsys):
    rig.make().run(max_cycles=3)
    assert "heap free" not in capsys.readouterr().out
