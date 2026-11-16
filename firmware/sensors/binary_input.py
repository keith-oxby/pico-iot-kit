"""Generic binary digital-input sensor driver.

Covers switches, PIR motion sensors, and reed switches with one driver --
anything that's simply HIGH/LOW on a GPIO pin. v1 sensor #3. Checklist
item 6 (docs/architecture.md, section 11).

The internal pull-up is enabled, so a dry contact wired between the pin and
GND reads HIGH when open and LOW when closed. With `invert=False`, HIGH is
"ON"; set `invert=True` for contacts to GND where closed should mean "ON".
"""

from .base import SensorAdapter


class BinaryInputSensor(SensorAdapter):
    """Any simple on/off digital input (switch, PIR, reed switch, ...)."""

    def __init__(self, name, pin, invert=False):
        super().__init__(name, pin=pin, invert=invert)
        import machine

        self.invert = invert
        self._pin = machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)

    def read(self):
        on = bool(self._pin.value()) != self.invert
        return {"state": "ON" if on else "OFF"}

    @property
    def topic(self):
        return self.name

    def discovery_payload(self):
        return [
            {
                "component": "binary_sensor",
                "key": "state",
                "name": self.name,
                "value_template": "{{ value_json.state }}",
                "payload_on": "ON",
                "payload_off": "OFF",
            }
        ]
