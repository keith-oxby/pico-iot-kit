"""Abstract base class every sensor driver implements.

A SensorAdapter wraps exactly one physical device and exposes it in a
uniform shape to the Application Runtime (firmware/runtime/device.py). The
runtime never knows *how* a reading is taken -- only that `.read()` returns
a dict (one key per measurement) and `.topic` names it. A single device may
report several measurements (e.g. a DHT11 gives temperature and humidity).

See docs/adding-a-sensor.md for the contributor guide.
"""


class SensorAdapter:
    """Base class for all sensor drivers.

    Subclasses must implement `read()` and `topic`, and should implement
    `discovery_payload()` if the sensor is meant to auto-register with
    Home Assistant via MQTT discovery.
    """

    def __init__(self, name, **options):
        """
        Args:
            name: a human-readable name for this sensor instance, used in
                topics and discovery payloads (e.g. "kitchen_temp").
            **options: bus/pin settings for the concrete driver -- `pin` for
                GPIO sensors; `sda`, `scl`, `i2c_id`, `address` for I2C
                sensors. Stored on `self.options`.
        """
        self.name = name
        self.options = options

    def read(self):
        """Take a reading and return a dict with one key per measurement,
        each value JSON-serializable.

        Example: {"temperature_c": 21.4, "humidity_pct": 40.2}
        """
        raise NotImplementedError

    @property
    def topic(self):
        """Topic segment downstream adapters publish this sensor's readings
        on. Must be unique per sensor instance, so drivers return `self.name`.
        """
        raise NotImplementedError

    def discovery_payload(self):
        """Return a list of Home Assistant MQTT discovery entries, one per
        key of `read()`, or None if this sensor type doesn't support HA
        discovery yet.

        Each entry holds only what the sensor knows: `component` ("sensor"
        or "binary_sensor"), `key` (the read() key it describes), and the
        HA fields `name`, `value_template`, and optionally `device_class`,
        `unit_of_measurement`, `state_class`. The backend adds the rest
        (state_topic, unique_id, device info) and drops `component`/`key`.
        """
        return None
