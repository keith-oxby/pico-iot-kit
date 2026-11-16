import pytest

from firmware.sensors.aht10 import Aht10Sensor

# status 0x1C (calibrated), humidity raw 0x80000 (50 %), temperature raw
# 0x66666 (~30 C)
FRAME = bytes([0x1C, 0x80, 0x00, 0x06, 0x66, 0x66])


class FakeI2C:
    def __init__(self, status=0x08, frames=(FRAME,)):
        self.writes = []
        self.status = status
        self.frames = list(frames)

    def writeto(self, address, data):
        self.writes.append((address, bytes(data)))

    def readfrom(self, address, n):
        if n == 1:
            return bytes([self.status])
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


@pytest.fixture
def i2c(fake_hardware):
    bus = FakeI2C()
    fake_hardware["machine"].I2C.return_value = bus
    return bus


def make(**kwargs):
    kwargs.setdefault("sda", 0)
    kwargs.setdefault("scl", 1)
    return Aht10Sensor("desk", sleep=lambda s: None, **kwargs)


def test_init_command_is_sent_and_calibration_checked(i2c):
    make()
    assert i2c.writes == [(0x38, b"\xe1\x08\x00")]


def test_uncalibrated_sensor_raises_a_clear_error(i2c):
    i2c.status = 0x00
    with pytest.raises(OSError, match="AHT20"):
        make()


def test_read_converts_the_datasheet_formulas(i2c):
    reading = make().read()
    assert reading == {"temperature_c": 30.0, "humidity_pct": 50.0}
    assert i2c.writes[-1] == (0x38, b"\xac\x33\x00")


def test_extremes(i2c):
    i2c.frames = [bytes([0x1C, 0x00, 0x00, 0x00, 0x00, 0x00])]
    assert make().read() == {"temperature_c": -50.0, "humidity_pct": 0.0}
    i2c.frames = [bytes([0x1C, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])]
    reading = make().read()
    assert reading["humidity_pct"] == 100.0
    assert reading["temperature_c"] == 150.0


def test_busy_then_ready_retries(i2c):
    busy = bytes([0x9C, 0, 0, 0, 0, 0])
    i2c.frames = [busy, busy, FRAME]
    assert make().read()["humidity_pct"] == 50.0


def test_stuck_busy_times_out(i2c):
    i2c.frames = [bytes([0x9C, 0, 0, 0, 0, 0])]
    with pytest.raises(OSError, match="timed out"):
        make().read()


@pytest.mark.parametrize("sda,scl,bus", [(0, 1, 0), (4, 5, 0), (20, 21, 0), (2, 3, 1), (6, 7, 1), (26, 27, 1)])
def test_bus_id_is_derived_from_the_pins(fake_hardware, i2c, sda, scl, bus):
    make(sda=sda, scl=scl)
    args, kwargs = fake_hardware["machine"].I2C.call_args
    assert args == (bus,)
    assert kwargs["freq"] == 100000


def test_explicit_bus_and_address_are_used(fake_hardware, i2c):
    make(sda=2, scl=3, i2c_id=1, address=0x39)
    assert fake_hardware["machine"].I2C.call_args.args == (1,)
    assert i2c.writes[0][0] == 0x39


def test_discovery_has_one_entry_per_reading_key(i2c):
    sensor = make()
    assert [e["key"] for e in sensor.discovery_payload()] == list(sensor.read())
    assert sensor.topic == "desk"
