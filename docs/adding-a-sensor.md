# Adding a new sensor type

(See also `firmware/sensors/README.md` for the short version.)

pico-iot-kit's sensor layer is deliberately thin: the Application Runtime
(`firmware/runtime/device.py`) only ever calls `sensor.read()` and reads
`sensor.topic` on whatever objects `firmware/sensors/__init__.py` hands it.
It never knows which concrete sensor type it's talking to.

## Steps

1. **Create the driver.** Add `firmware/sensors/your_sensor.py` with a class
   that subclasses `SensorAdapter` (`firmware/sensors/base.py`) and implements:
   - `read()` -> a dict with one key per measurement, e.g.
     `{"temperature_c": 21.4, "humidity_pct": 47.2}`
   - `topic` (a property) -> the base topic string, e.g. `"humidity"`
   - `discovery_payload()` (optional) -> a list of Home Assistant MQTT
     discovery dicts, one per key of `read()`, or `None` if you're not
     supporting HA discovery for this sensor yet

   The constructor is `__init__(self, name, **options)`; bus settings (`pin`,
   or `sda`/`scl`/`i2c_id`/`address`) arrive in `options`.

   Names, `topic` and the keys of `read()` end up in MQTT topics, so keep
   them to letters, digits, `-` and `_` (the schema enforces this for
   names; the adapters re-check every segment and refuse `/`, `+` and `#`).
   Each `discovery_payload()` entry needs a `component` (`"sensor"` or
   `"binary_sensor"`) and the `key` of `read()` it describes, plus whatever
   Home Assistant needs to display it (`name`, `value_template`,
   `device_class`, `unit_of_measurement`, ...). The Home Assistant adapter
   adds the state topic, a unique id, the device block and availability, and
   drops `component` and `key`. When a user later removes the sensor, the
   adapter clears its entity from Home Assistant by itself.

2. **Register it.** Add an entry to `SENSOR_TYPES` in
   `firmware/sensors/__init__.py` mapping a config `type` string (e.g.
   `"bme280"`) to your new class.

3. **Add it to the config schema.** Add a row to `SENSOR_FIELDS` in
   `firmware/runtime/config_schema.py` listing your sensor's config fields
   (and a `SENSOR_CHECKS` entry for any cross-field rules, like I2C pin
   pairs). Every field except `type` and `name` is passed to your
   constructor as a keyword argument, and unknown fields are rejected.

4. **Expose it in the setup form.** Add your sensor type to the sensor
   selector in `firmware/provisioning/templates/setup.html` so users can
   pick it without touching code.

5. **Test it.** Add a test under `tests/test_sensors/` that exercises
   `read()` against a mocked pin/hardware interface.

6. **Document any wiring.** If your sensor needs specific wiring (pull-up
   resistors, I2C address, etc.), add a short note to your driver's module
   docstring.

No other file in the codebase should need to change. If you find yourself
editing `firmware/runtime/device.py` to add a sensor, that's a sign the
`SensorAdapter` interface itself needs to grow a new optional method rather
than the runtime special-casing your sensor.
