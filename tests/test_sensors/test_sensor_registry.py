import pytest

from firmware.runtime import config_schema
from firmware.sensors import SENSOR_TYPES, load_sensor_adapters


def test_registry_and_schema_agree_on_sensor_types():
    assert set(SENSOR_TYPES) == set(config_schema.SENSOR_FIELDS)


def test_loader_builds_each_type_with_its_options(fake_hardware):
    adapters = load_sensor_adapters([
        {"type": "dht11", "name": "lounge", "pin": 4},
        {"type": "binary_input", "name": "door", "pin": 15, "invert": True},
    ])
    assert [a.name for a in adapters] == ["lounge", "door"]
    assert adapters[1].invert is True


def test_loader_plumbs_i2c_options(fake_hardware):
    fake_hardware["machine"].I2C.return_value.readfrom.return_value = b"\x08"
    (sensor,) = load_sensor_adapters(
        [{"type": "aht10", "name": "desk", "sda": 2, "scl": 3, "i2c_id": 1, "address": 0x39}]
    )
    assert sensor.options == {"sda": 2, "scl": 3, "i2c_id": 1, "address": 0x39}


def test_unknown_type_raises(fake_hardware):
    with pytest.raises(ValueError):
        load_sensor_adapters([{"type": "ds18b20", "name": "x", "pin": 4}])


def test_every_schema_valid_i2c_pair_maps_to_the_bus_the_driver_derives():
    for bus, (sda_pins, _) in config_schema.I2C_BUSES.items():
        for sda in sda_pins:
            assert (sda // 2) % 2 == bus
