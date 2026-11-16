"""AHT10 temperature + humidity sensor over I2C.

v1 sensor #2. Checklist item 6 (docs/architecture.md, section 11).
Wiring: SDA/SCL to a valid RP2040 I2C pin pair (the config schema checks
this), 3V3 and GND. Default address 0x38 (0x39 with the ADDR pin high).

Only the AHT10 command set is supported. Boards sold as "AHT10" are often
AHT20/AHT21, which use a different init command (0xBE instead of 0xE1);
those fail at startup with a clear "not calibrated" error rather than
returning bad data. An `aht20` type would be a separate driver.
"""

import time

from .base import SensorAdapter

DEFAULT_ADDRESS = 0x38
_CMD_INIT = b"\xe1\x08\x00"
_CMD_MEASURE = b"\xac\x33\x00"
_STATUS_BUSY = 0x80
_STATUS_CALIBRATED = 0x08
_MEASURE_WAIT_S = 0.08
_BUSY_RETRIES = 5


class Aht10Sensor(SensorAdapter):
    def __init__(self, name, sda, scl, i2c_id=None, address=DEFAULT_ADDRESS, sleep=time.sleep):
        super().__init__(name, sda=sda, scl=scl, i2c_id=i2c_id, address=address)
        import machine

        if i2c_id is None:
            # RP2040: I2C0 uses SDA pins 0,4,8,... and I2C1 uses 2,6,10,...
            i2c_id = (sda // 2) % 2
        self._address = address
        self._sleep = sleep
        self._i2c = machine.I2C(i2c_id, sda=machine.Pin(sda), scl=machine.Pin(scl), freq=100000)
        self._init_sensor()

    def _init_sensor(self):
        self._i2c.writeto(self._address, _CMD_INIT)
        self._sleep(0.02)
        status = self._i2c.readfrom(self._address, 1)[0]
        if not status & _STATUS_CALIBRATED:
            raise OSError("AHT10 not calibrated (is this an AHT20/AHT21?)")

    def read(self):
        self._i2c.writeto(self._address, _CMD_MEASURE)
        for _ in range(_BUSY_RETRIES):
            self._sleep(_MEASURE_WAIT_S)
            data = self._i2c.readfrom(self._address, 6)
            if not data[0] & _STATUS_BUSY:
                break
        else:
            raise OSError("AHT10 measurement timed out")

        humidity_raw = ((data[1] << 16) | (data[2] << 8) | data[3]) >> 4
        temperature_raw = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
        return {
            "temperature_c": round(temperature_raw * 200 / 1048576 - 50, 1),
            "humidity_pct": round(humidity_raw * 100 / 1048576, 1),
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
