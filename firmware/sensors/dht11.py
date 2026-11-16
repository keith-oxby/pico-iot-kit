"""DHT11 temperature + humidity sensor (single GPIO, proprietary protocol).

v1 sensor #1. Checklist item 6 (docs/architecture.md, section 11). Uses
MicroPython's built-in `dht` module. Wiring: data pin to the configured
GPIO, with a ~10k pull-up to 3V3 (most breakout boards include one).

The DHT11 needs about a second between measurements; the default poll
interval is well above that. Integer resolution only.
"""

from .base import SensorAdapter


class Dht11Sensor(SensorAdapter):
    def __init__(self, name, pin):
        super().__init__(name, pin=pin)
        import dht
        import machine

        self._dht = dht.DHT11(machine.Pin(pin))

    def read(self):
        # measure() raises OSError on timeout or checksum failure; the
        # runtime skips this sensor for the cycle.
        self._dht.measure()
        return {
            "temperature_c": self._dht.temperature(),
            "humidity_pct": self._dht.humidity(),
        }

    @property
    def topic(self):
        return self.name

    def discovery_payload(self):
        return [
            {
                "component": "sensor",
                "key": "temperature_c",
                "name": "%s temperature" % self.name,
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "state_class": "measurement",
                "value_template": "{{ value_json.temperature_c }}",
            },
            {
                "component": "sensor",
                "key": "humidity_pct",
                "name": "%s humidity" % self.name,
                "device_class": "humidity",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "value_template": "{{ value_json.humidity_pct }}",
            },
        ]
