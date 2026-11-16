"""Sensor adapter registry.

Maps the sensor `type` string used in config.json to its SensorAdapter
subclass. Adding a new sensor type (docs/adding-a-sensor.md) means adding
one new file next to this one and registering it here -- no other file
needs to change.
"""

from .aht10 import Aht10Sensor
from .base import SensorAdapter
from .binary_input import BinaryInputSensor
from .dht11 import Dht11Sensor

SENSOR_TYPES = {
    "dht11": Dht11Sensor,
    "aht10": Aht10Sensor,
    "binary_input": BinaryInputSensor,
}


def load_sensor_adapters(sensor_config):
    """Build SensorAdapter instances from the "sensors" section of config.json.

    Args:
        sensor_config: list of dicts like
            {"type": "dht11", "pin": 4, "name": "kitchen_temp"}
        Every field except `type` and `name` is passed to the constructor
        as a keyword argument; config_schema rejects unknown fields so a
        bad config falls back to SETUP instead of failing here.

    Returns:
        list of SensorAdapter instances.
    """
    adapters = []
    for entry in sensor_config:
        cls = SENSOR_TYPES.get(entry["type"])
        if cls is None:
            raise ValueError("Unknown sensor type: %r" % entry["type"])
        options = {k: v for k, v in entry.items() if k not in ("type", "name")}
        adapters.append(cls(name=entry.get("name", entry["type"]), **options))
    return adapters
