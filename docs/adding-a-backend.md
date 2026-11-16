# Adding a new downstream backend

(See also `firmware/adapters/README.md` for the short version.)

The Application Runtime only ever calls `adapter.connect()`,
`adapter.publish(topic, reading)`, `adapter.poll_commands()`, and
`adapter.announce(sensors)` on whatever `firmware/adapters/__init__.py`
hands it. It never knows which concrete backend it's talking to.

## Steps

1. **Create the adapter.** Add `firmware/adapters/your_backend.py` with a
   class subclassing `DownstreamAdapter` (`firmware/adapters/base.py`) and
   implementing `connect()`, `publish()`, `poll_commands()`, and optionally
   `announce()`. If your backend is MQTT-based, consider subclassing
   `LocalMqttAdapter` instead of `DownstreamAdapter` directly (see how
   `home_assistant.py` does this) to avoid re-implementing the MQTT
   transport.

2. **Register it, and add it to the config schema.** Add an entry to `BACKEND_TYPES` in
   `firmware/adapters/__init__.py` mapping a config `type` string (e.g.
   `"aws_iot"`) to your new class.
   Also add a row to `BACKEND_FIELDS` in `firmware/runtime/config_schema.py`
   listing the config fields your backend accepts; a test fails if the
   registry and the schema disagree.

3. **Expose it in the setup form.** Add your backend to the backend-type
   selector in `firmware/provisioning/templates/setup.html`, along with
   whatever fields it needs (host/port/credentials, a connection string,
   certificate upload, etc.).

   If your backend takes the same host/port/credentials as MQTT, add its
   type to `_MQTT_TYPES` in `firmware/provisioning/form.py`. Any other
   fields need handling in `_build_backend` there, with the same names as
   the form inputs.

4. **Default to verified, encrypted transport.** TLS (or your backend's
   equivalent) must be the default; a weaker mode is only ever an explicit
   opt-in. MQTT-based backends should reuse `firmware/adapters/tls.py`: it
   maps the `use_tls` and `tls_verify` settings to the three modes
   (verified, encrypted but not verified, plaintext), builds the
   `SSLContext`, and refuses to connect in verified mode without a usable CA
   (`/ca.pem`, installed from a PC) instead of falling back. Verified mode
   also needs the clock set, which `firmware/runtime/clock.py` does over
   NTP. Never log config contents, since they hold credentials. See
   `docs/security.md`.

   The runtime injects `device_id` (from the hardware, never from the saved
   config) and `poll_interval_s` into your backend's config. Build topics
   only from `device_id` and validated names, and treat anything received as
   untrusted: bound its size and never execute it.

5. **Mind the watchdog.** The board resets about a minute after the
   watchdog is last fed. Any wait that can last more than a few seconds
   (retries, backoff) must use `watchdog.nap()` or call `watchdog.checkin()`
   in a loop (`firmware/watchdog.py`).

6. **Test it.** Add a test under `tests/test_adapters/` using a mocked
   network connection (the `fake_mqtt` and `fake_hardware` fixtures in
   `tests/conftest.py` and the fake `ssl` in `tests/test_adapters/test_tls.py`
   are good starting points), including the failure paths: a refused
   connection, a dropped connection, and bad input.

AWS IoT is the documented first candidate for this -- its device SDK and
MQTT topic conventions are well documented and it fits the same "connection
string / token-based auth over MQTT" shape planned for the Azure IoT Hub
adapter (`azure_iot_hub.py`, currently an unregistered stub), so it's a
reasonable reference point for your own implementation.
