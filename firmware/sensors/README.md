# Adding a new sensor

1. Create `firmware/sensors/your_sensor.py` implementing `SensorAdapter`
   (see `base.py`): `read()`, `topic`, and optionally `discovery_payload()`.
2. Register it in `firmware/sensors/__init__.py`'s `SENSOR_TYPES` dict.
3. Add a row for it to `SENSOR_FIELDS` in `firmware/runtime/config_schema.py`.
4. Add it to the setup form's sensor-type list (`firmware/provisioning/templates/`)
   so users can select it without editing code.
5. Add a test under `tests/test_sensors/`.

No other file should need to change. See `docs/adding-a-sensor.md` for the
full contributor guide.
