import datetime

import pytest

from tools.soak import soak


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    wall = datetime.datetime(2026, 10, 24, 12, 0, 0, tzinfo=datetime.timezone.utc)
    s = soak.Soak(str(tmp_path), 30, now=clock, clock=lambda: wall + datetime.timedelta(seconds=clock.t))
    s.clock_fake = clock
    return s


def mqtt(s, topic, payload="{}", retained=0, advance=30):
    s.clock_fake.t += advance
    s.mqtt_line("%d\t%s\t%s\n" % (retained, topic, payload))


def events(s):
    s.events.flush()
    return open(s.events.name).read()


def test_regular_readings_are_not_gaps(rig):
    for _ in range(10):
        mqtt(rig, "dev/dht11")
    assert rig.counters.get("gaps", 0) == 0
    assert rig.tracker.counts["dev/dht11"] == 10


def test_a_silence_of_two_intervals_is_a_gap_and_is_logged(rig):
    mqtt(rig, "dev/dht11")
    mqtt(rig, "dev/dht11", advance=61)
    assert rig.counters["gaps"] == 1
    assert "GAP dev/dht11: 61 s" in events(rig)


def test_a_slightly_late_reading_is_not_a_gap(rig):
    mqtt(rig, "dev/dht11")
    mqtt(rig, "dev/dht11", advance=47)  # under 1.6 intervals
    assert rig.counters.get("gaps", 0) == 0


def test_gaps_are_tracked_per_topic(rig):
    mqtt(rig, "dev/dht11", advance=0)
    mqtt(rig, "dev/aht10", advance=0)
    mqtt(rig, "dev/dht11", advance=100)
    assert [t for t, _ in rig.tracker.gaps] == ["dev/dht11"]


def test_retained_and_discovery_messages_are_ignored_for_gaps(rig):
    mqtt(rig, "dev/dht11", retained=1)
    mqtt(rig, "homeassistant/sensor/dev/x/config")
    assert rig.tracker.counts == {}


def test_status_changes_are_logged_once_and_offline_is_counted(rig):
    mqtt(rig, "dev/status", "online", retained=1)
    mqtt(rig, "dev/status", "online")
    mqtt(rig, "dev/status", "offline")
    mqtt(rig, "dev/status", "online")
    log = events(rig)
    assert log.count("status online") == 2 and log.count("status offline") == 1
    assert rig.counters["offline"] == 1


def test_malformed_lines_are_ignored(rig):
    rig.mqtt_line("garbage\n")
    rig.mqtt_line("\n")
    assert rig.tracker.counts == {}


@pytest.mark.parametrize("line,label", [
    ("watchdog: armed (60 s check-in budget)", "boot"),
    ("watchdog: no check-in for 60 s, letting the watchdog reset the device", "watchdog warning"),
    ("device: no connection for 15 minutes, restarting", "stall reset"),
    ("app: crashed, restarting in 30s", "crash"),
    ("device: connect failed, retrying in 4s - OSError 110", "connect failed"),
    ("mqtt: connecting to 192.168.0.10:8883 (TLS, broker verified)", None),
])
def test_serial_lines_are_classified(line, label):
    assert soak.classify_serial_line(line)[0] == label


def test_heap_is_parsed_and_summarised(rig):
    for n in (170000, 168000, 171000):
        rig.serial_line("device: heap free %d bytes" % n)
    text = rig.summary()
    assert "first 170000, min 168000, max 171000, last 171000, samples 3" in text


def test_the_summary_counts_boots_and_resets(rig):
    rig.serial_line("watchdog: armed (60 s check-in budget)")
    rig.serial_line("watchdog: armed (60 s check-in budget)")
    rig.serial_line("device: no connection for 15 minutes, restarting")
    mqtt(rig, "dev/dht11")
    text = rig.write_summary()
    assert "boots seen: 2" in text and "stall resets: 1" in text
    assert "dev/dht11" in text
    assert open(rig.out_dir + "/soak-summary.txt").read() == text


def test_a_burst_of_connect_failures_is_not_logged_line_by_line(rig):
    for _ in range(50):
        rig.serial_line("device: connect failed, retrying in 60s - OSError 110")
    assert len([line for line in events(rig).splitlines() if "[connect failed]" in line]) == 3
    assert rig.counters["connect failed"] == 50
