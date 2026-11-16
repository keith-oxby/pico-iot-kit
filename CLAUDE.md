# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MicroPython firmware for Raspberry Pi Pico W (RP2040; the Pico 2 W / RP2350 is planned but untested, and no image is built for it): flash one frozen `.uf2`, then configure Wi-Fi, sensors, and backend from a phone via a setup AP + captive portal. v1 is implemented and verified on a Pico W; the one remaining stub is `adapters/azure_iot_hub.py` (deferred, not registered).

`docs/architecture.md` is the authoritative spec. Its section 11 **Implementation Checklist** is the ordered task list for v1; stub docstrings reference checklist item numbers. Each checklist item should land as its own commit/PR so the layer separation stays visible in history.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
- Host-side tests (what CI runs): `pytest tests/`
- Lint (what CI runs): `ruff check .` (config in `pyproject.toml`)
- Single test: `pytest tests/test_sensors/test_dht11.py::test_name`
- Fast on-device iteration: copy changed `.py` files to a dev board with `mpremote` instead of rebuilding the `.uf2`.
- Frozen `.uf2` build: `build/build.sh` (needs podman or docker; builds the pinned `build/docker/` image on first use, then the Pico W image into `build/out/`, about 15 s after that; needs no network once the image exists). Reserved for releases and final hardware verification.

Current state: CI (`ci.yml`) runs ruff and pytest. `release.yml` builds the Pico W `.uf2` (start it by hand from the Actions tab to test, nothing is published; a pushed `v*` tag also creates a GitHub Release, a pre-release if the tag has a suffix). Test files need unique basenames (the test directories have no `__init__.py`). Tests that touch hardware modules request the `fake_hardware` fixture from `tests/conftest.py`.

## Architecture

Three layers that must stay independent (the project's core design goal):

1. **Provisioning** (`firmware/provisioning/`) — boot-time only. `manager.start()` watches BOOTSEL for a 3 s window after boot and, if it is pressed and held 5 s (→ `config_store.clear()`; holding at power-up would trigger the USB bootloader instead), then `config_store.load()`. Valid config → returned to caller (RUN mode). Otherwise SETUP mode: `ap_portal` starts a WPA2 AP `PicoIoTKit-<last 4 hex of unique_id>` and a captive-portal HTTP + DNS server at 192.168.4.1; on a valid submit it saves via `config_store.save`, sends the confirmation page and returns the config, and `manager` then restarts the device. Socket glue is in `ap_portal.py`; request routing (`portal_http.py`), form → config (`form.py`) and the DNS responder (`captive_dns.py`) are pure and host-tested. Knows nothing about sensors or backends.
2. **Runtime** (`firmware/runtime/`) — `Device` builds sensors/backend from config via the registries, then loops: join Wi-Fi (`runtime/wifi.py`) → `connect()` → `announce(sensors)` → forever { `read()` each sensor → `publish(sensor.topic, reading)` → `poll_commands()` → sleep `poll_interval_s` }. A failing `read()` skips that sensor; a failing `publish`/`poll_commands` reconnects (Wi-Fi, backend, announce) with capped exponential backoff; policy is documented in `runtime/device.py`. `device_id` (from `machine.unique_id()`) and `poll_interval_s` are injected into the backend config by the runtime; `device_id` is never read from the saved config. `firmware/runtime/__init__.py` is deliberately empty: `runtime.device` imports the adapter registry and the adapters import `runtime.clock`/`ca_store`, so importing `device` from the package would create an import cycle (`tests/test_imports.py` guards this).
3. **Adapters** — two families, each behind an interface and a type-string registry:
   - `firmware/sensors/`: `SensorAdapter` (`read()` returns a dict, one key per measurement; `topic`; `discovery_payload()` returns a list, one entry per key), registry `SENSOR_TYPES`. v1 sensors: DHT11, AHT10, binary input.
   - `firmware/adapters/`: `DownstreamAdapter` (`connect`, `publish`, `poll_commands`, optional `announce`), registry `BACKEND_TYPES`. `HomeAssistantAdapter` subclasses `LocalMqttAdapter` to reuse transport and only adds discovery; Azure IoT Hub is a separate MQTT/SAS-token implementation.

Adding a sensor or backend = one new file + one registry entry + a row in the `config_schema` field tables + a selector entry in `firmware/provisioning/templates/setup.html`. If you find yourself editing `runtime/device.py` for a specific sensor/backend, grow the interface instead.

Boot flow: `firmware/main.py` is the only entry point and calls `firmware.app.main()`, which runs `provisioning.start()` then `runtime.run(config)` and, on any unhandled `Exception`, logs it, waits 30 s and resets the board (Ctrl-C still reaches the REPL). There is no `boot.py`: MicroPython executes `boot.py` itself without registering it as a module, so a `main.py` doing `import boot` ran it a second time (confirmed on a Pico W).

### Config

Single JSON file `/config.json` on flash, shape `{"wifi": {ssid, password}, "backend": {"type": ..., ...}, "sensors": [{"type", "pin", "name", ...}], "poll_interval_s"?}`. `runtime/config_schema.validate()` is used on both save and load; any load failure (missing, bad JSON, invalid) returns `None` → SETUP mode, never a crash. Saves are atomic (write `.tmp`, then `os.rename`). Keep `config_store`'s interface narrow (`exists/load/save/clear`) — an encrypted-at-rest backend for RP2350 is planned to swap in behind it.

## Constraints and gotchas

- Code runs on MicroPython: no `abc`, `typing`, dataclasses, etc. Interfaces are plain base classes raising `NotImplementedError`. Hardware modules (`machine`, `network`, `onewire`, `ds18x20`, `umqtt`) don't exist on the host, so host tests must mock them (e.g. inject fakes into `sys.modules`).
- Imports use a `firmware.` package prefix (`from firmware.provisioning import ...`) everywhere, including `main.py`. The frozen build (`build/stage.py` + `build/manifest.py`) freezes the whole `firmware/` package, with `firmware/main.py` at the root, and `setup.html` ships as a generated module (`firmware/provisioning/_setup_page.py`, never committed) that `ap_portal.load_page()` prefers over the file. On a board, a `firmware/` folder on the filesystem shadows the frozen package, but a frozen `main.py` always wins over one on the filesystem.
- `config_store` (provisioning) imports `runtime.config_schema` — the one existing cross-layer dependency.
- `load_sensor_adapters` passes every sensor field except `type`/`name` to the constructor as a keyword argument, so `config_schema` rejects unknown sensor fields (a `TypeError` at boot would crash-loop instead of falling back to SETUP). GPIO 23/24/25 are rejected because the Pico W's Wi-Fi chip uses them.
- The firmware arms a hardware watchdog at boot (`firmware/watchdog.py`): waits that can last more than a few seconds must use `watchdog.nap()` (or call `watchdog.checkin()` in a loop), or the board resets about a minute later. Because an `mpremote` connection soft-resets the board and leaves the watchdog armed, the board resets ~8 s after any `mpremote` session unless `/no_wdt` exists on it (see CONTRIBUTING.md). `Device` also restarts the board after 15 minutes without a successful connection.
- The setup page must be a single static HTML page with a plain form POST — no JS framework.

## Security requirements (see `docs/security.md`)

Changes to `firmware/provisioning/` or adapter transport must be checked against `docs/security.md`. Non-negotiables: setup AP is never open (`DEFAULT_AP_PASSWORD`); MQTT uses TLS by default and plaintext is an explicit opt-in (`use_tls`); setup mode can only be re-entered via physical BOOTSEL hold, never a network endpoint; invalid config on load falls back to SETUP. Encrypted-at-rest secrets and OTA are explicitly deferred non-goals for v1.
