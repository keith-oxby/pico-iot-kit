import pytest

from firmware.sensors.dht11 import Dht11Sensor


@pytest.fixture
def dht(fake_hardware):
    return fake_hardware["dht"].DHT11.return_value


def test_pin_is_wired_to_the_dht_driver(fake_hardware, dht):
    Dht11Sensor("lounge", pin=4)
    fake_hardware["machine"].Pin.assert_called_once_with(4)
    fake_hardware["dht"].DHT11.assert_called_once_with(fake_hardware["machine"].Pin.return_value)


def test_read_returns_temperature_and_humidity(dht):
    dht.temperature.return_value = 23
    dht.humidity.return_value = 41
    sensor = Dht11Sensor("lounge", pin=4)
    assert sensor.read() == {"temperature_c": 23, "humidity_pct": 41}
    dht.measure.assert_called_once_with()


def test_measure_failure_propagates_for_the_runtime_to_handle(dht):
    dht.measure.side_effect = OSError(110)
    with pytest.raises(OSError):
        Dht11Sensor("lounge", pin=4).read()


def test_topic_is_the_sensor_name_so_two_sensors_do_not_collide(dht):
    assert Dht11Sensor("a", pin=4).topic == "a"
    assert Dht11Sensor("b", pin=5).topic == "b"


def test_options_are_recorded(dht):
    assert Dht11Sensor("lounge", pin=4).options == {"pin": 4}


def test_discovery_has_one_entry_per_reading_key(dht):
    dht.temperature.return_value = 23
    dht.humidity.return_value = 41
    sensor = Dht11Sensor("lounge", pin=4)
    entries = sensor.discovery_payload()
    assert [e["key"] for e in entries] == list(sensor.read())
    assert {e["device_class"] for e in entries} == {"temperature", "humidity"}
    assert all(e["component"] == "sensor" for e in entries)
    assert all("value_json.%s" % e["key"] in e["value_template"] for e in entries)
