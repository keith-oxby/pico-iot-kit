import pytest

from firmware.sensors.base import SensorAdapter


def test_constructor_stores_name_and_options():
    sensor = SensorAdapter("kitchen", sda=0, scl=1, i2c_id=0)
    assert sensor.name == "kitchen"
    assert sensor.options == {"sda": 0, "scl": 1, "i2c_id": 0}


def test_options_default_to_empty():
    assert SensorAdapter("x").options == {}


def test_read_must_be_implemented():
    with pytest.raises(NotImplementedError):
        SensorAdapter("x").read()


def test_topic_must_be_implemented():
    with pytest.raises(NotImplementedError):
        _ = SensorAdapter("x").topic


def test_discovery_payload_defaults_to_none():
    assert SensorAdapter("x").discovery_payload() is None


def test_subclass_can_return_multi_value_reading():
    class Fake(SensorAdapter):
        def read(self):
            return {"temperature_c": 21.4, "humidity_pct": 40.2}

        @property
        def topic(self):
            return self.name

    sensor = Fake("lounge", pin=4)
    assert sensor.read() == {"temperature_c": 21.4, "humidity_pct": 40.2}
    assert sensor.topic == "lounge"
