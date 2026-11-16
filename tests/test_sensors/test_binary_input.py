import pytest

from firmware.sensors.binary_input import BinaryInputSensor


@pytest.fixture
def pin(fake_hardware):
    return fake_hardware["machine"].Pin.return_value


def test_pin_is_an_input_with_pull_up(fake_hardware, pin):
    BinaryInputSensor("door", pin=15)
    machine = fake_hardware["machine"]
    machine.Pin.assert_called_once_with(15, machine.Pin.IN, machine.Pin.PULL_UP)


@pytest.mark.parametrize("level,invert,state", [
    (1, False, "ON"),
    (0, False, "OFF"),
    (1, True, "OFF"),
    (0, True, "ON"),
])
def test_state_follows_pin_level_and_invert(pin, level, invert, state):
    pin.value.return_value = level
    assert BinaryInputSensor("door", pin=15, invert=invert).read() == {"state": state}


def test_topic_and_options(pin):
    sensor = BinaryInputSensor("door", pin=15, invert=True)
    assert sensor.topic == "door"
    assert sensor.options == {"pin": 15, "invert": True}


def test_discovery_is_a_binary_sensor(pin):
    sensor = BinaryInputSensor("door", pin=15)
    (entry,) = sensor.discovery_payload()
    assert entry["component"] == "binary_sensor"
    assert entry["key"] in sensor.read()
    assert (entry["payload_on"], entry["payload_off"]) == ("ON", "OFF")
