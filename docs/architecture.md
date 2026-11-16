# pico-iot-kit — Architecture & Project Specification

This is the full specification used to scaffold this repository. Claude Code
(or any contributor) should treat the **Implementation Checklist** at the end
as the ordered task list for building out v1.

## 1. Overview & Goals

**Mission:** let anyone download a single firmware image, flash it to a
Raspberry Pi Pico W (the Pico 2 W is planned but not yet tested), and configure it entirely from their phone
or PC over Wi-Fi — no code editing, no IDE, no USB file copying after the
initial flash.

**What success looks like:** a user flashes `pico-iot-kit.uf2`, the board
starts its own Wi-Fi access point, the user connects from a phone and fills in
a form (home Wi-Fi credentials, which sensor is on which pin, which
downstream backend to use), the board reboots onto their network and starts
publishing sensor data / accepting commands.

**Non-goals for v1:**
- Over-the-air (OTA) firmware updates — re-flashing over USB is acceptable for now
- Encrypted-at-rest secrets storage — deferred because the primary hardware
  target is the Pico 1 (RP2040), which lacks the secure-storage primitives the
  Pico 2 (RP2350) has; the storage module should be designed so this can be
  added later without breaking the config format
- AWS IoT, Google Cloud IoT, Azure IoT Hub, or other cloud backends — only
  private MQTT (including Home Assistant) ships in v1; the adapter interface must make adding these a contained,
  well-documented task
- Supporting boards other than Pico W / Pico 2 W
- A companion mobile app — all configuration happens through a browser
  against the device's own web server

## 2. End-User Experience

1. **Download** `pico-iot-kit-vX.Y.uf2` from the project's GitHub Releases page.
2. **Flash**: hold BOOTSEL, plug the Pico into a PC via USB, it mounts as a
   mass-storage drive, drag the `.uf2` onto it. The board reboots running the
   firmware.
3. **First boot — setup mode**: finding no saved Wi-Fi config, the device
   starts its own password-protected access point (SSID e.g.
   `PicoIoTKit-XXXX`, a random per-device suffix; a printed/documented
   default password).
4. **Connect**: the user joins that Wi-Fi network from their phone or PC. A
   captive-portal redirect (or a printed/documented IP, e.g. `192.168.4.1`,
   as a fallback on phones that don't auto-redirect) opens the setup page in
   their browser.
5. **Configure**, all through web forms:
   - Home Wi-Fi SSID + password
   - Downstream backend: Local MQTT broker / Home Assistant (MQTT discovery)
     — with the specific fields each needs, and how to secure the connection
     (verified TLS, encrypted but unverified, or plaintext)
   - Sensor assignment: for each of the supported v1 sensor types, which GPIO
     pin (if any) it's wired to
6. **Submit**: the device validates input, stores it, shows a confirmation
   page, and reboots.
7. **Normal operation**: the device joins the home Wi-Fi, connects to the
   configured backend, and starts running — publishing sensor readings
   and/or accepting commands, depending on sensor type.
8. **Reconfigure later**: shortly after power-up, while the LED is on, press
   and hold the BOOTSEL button for 5 seconds to force the device back into
   setup mode (step 3) without losing firmware or requiring a re-flash.
   (Holding BOOTSEL *at* power-up starts the USB bootloader instead.)

## 3. Architecture

Three layers, kept strictly independent:

- **Provisioning Manager** — owns boot-time logic only: checks for saved
  config, runs the setup AP + captive portal when none exists, writes
  validated config to storage, and handles the BOOTSEL-5s reset. It knows
  nothing about sensors or backends.
- **Application Runtime** — the `Device` base class and its main loop. Once
  config exists, it reads the sensor map and backend choice from storage,
  instantiates the matching adapters, and runs the read-sensors → publish →
  handle-commands loop. It knows nothing about *how* a sensor is read or
  *how* a backend talks — only that both conform to their respective
  interfaces.
- **Adapter layer** — two independent families of pluggable implementations,
  each behind an abstract interface: Sensor Adapters (reading hardware) and
  Downstream Adapters (talking to a backend). Neither family depends on the
  other, and both depend only on the interfaces the Application Runtime
  defines — not on each other's concrete classes.

```
Boot
  └─▶ Check config
        ├─ no config ──▶ Setup AP + captive portal ──▶ config submitted; reboot
        └─ config saved ──▶ Join network ──▶ Run adapters loop
```

This separation is the whole point of the project: a contributor adding a new
sensor touches only `firmware/sensors/`, and one adding a new backend touches
only `firmware/adapters/`, without either needing to understand the
provisioning flow.

## 4. Repository Structure

```
pico-iot-kit/
├── LICENSE                      # MIT
├── README.md
├── CONTRIBUTING.md              # how to add a sensor adapter / downstream adapter
├── firmware/
│   ├── main.py                  # the only entry point; calls app.main()
│   ├── app.py                   # provisioning, then runtime, with crash recovery (no boot.py)
│   ├── provisioning/
│   │   ├── __init__.py
│   │   ├── manager.py           # ProvisioningManager: state machine, BOOTSEL watch
│   │   ├── ap_portal.py         # AP bring-up + socket server (HTTP + DNS) glue
│   │   ├── portal_http.py       # pure request routing, probe redirects, page rendering
│   │   ├── form.py              # pure form body -> validated config
│   │   ├── captive_dns.py       # pure DNS responder (answers every query with the AP IP)
│   │   ├── config_store.py      # read/write validated config to flash
│   │   └── templates/           # setup-flow HTML pages
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── device.py            # Device base class, main loop
│   │   └── config_schema.py     # validates the full on-disk config shape
│   ├── sensors/
│   │   ├── __init__.py
│   │   ├── base.py              # SensorAdapter abstract base
│   │   ├── dht11.py             # v1 sensor #1 (temperature + humidity, single GPIO)
│   │   ├── aht10.py             # v1 sensor #2 (temperature + humidity, I2C)
│   │   ├── binary_input.py      # v1 sensor #3 (switch/PIR/reed, digital in)
│   │   └── README.md            # contributor guide: adding a new sensor
│   └── adapters/
│       ├── __init__.py
│       ├── base.py              # DownstreamAdapter abstract base
│       ├── local_mqtt.py
│       ├── home_assistant.py    # wraps local_mqtt.py, adds HA discovery payloads
│       ├── azure_iot_hub.py
│       └── README.md            # contributor guide: adding a new backend
├── build/
│   ├── manifest.py              # MicroPython manifest: board set + umqtt + the staged firmware/
│   ├── stage.py                 # stages firmware/ (and the generated setup page) for freezing
│   ├── build.sh                 # builds MicroPython + freezes app -> pico-iot-kit.uf2
│   └── docker/                  # containerized build environment (pinned versions)
├── tests/
│   ├── test_sensors/
│   ├── test_adapters/
│   └── test_provisioning/       # host-side tests using mocked network/flash
├── docs/
│   ├── architecture.md          # this file
│   ├── adding-a-sensor.md
│   ├── adding-a-backend.md
│   └── security.md
└── .github/
    └── workflows/
        ├── ci.yml                # lint + host-side tests on every PR
        └── release.yml           # builds and attaches the .uf2 to GitHub Releases on tag push
```

## 5. Provisioning Manager Spec

**State machine** (`provisioning/manager.py`):

1. On boot, `ConfigStore.exists()` checks flash for a saved, validated config file.
2. **No config found → SETUP mode:**
   - Start the CYW43 Wi-Fi chip in AP mode. SSID: `PicoIoTKit-<last 4 hex of
     the board's unique ID>`. Password: a fixed default documented in the
     README (e.g. `setupme123`), changeable by a future config option.
   - Start a lightweight web server (`ap_portal.py`) serving the setup form
     on `192.168.4.1`.
   - Where supported, respond to captive-portal detection requests
     (OS-specific probe URLs used by iOS/Android/Windows) by redirecting to
     the setup page, so most phones open it automatically.
   - On form submit: validate input (`config_schema.py`), write it via
     `ConfigStore.save()`, show a confirmation page, then reboot.
3. **Config found → RUN mode:** hand off to `runtime.run()` with the loaded config.
4. **BOOTSEL-5s reset** (checked first thing at startup in `app.main()`, before
   Wi-Fi init either way): the RP2040 boot ROM enters the USB bootloader if BOOTSEL is held at
   reset, so the hold can't start before our code runs. Instead, for the first
   3 seconds of every boot (LED on) the manager watches the button; if it is
   pressed in that window and then held continuously for ≥ 5 seconds, call
   `ConfigStore.clear()` (the LED blinks three times to confirm) and proceed
   into SETUP mode regardless of whether a config exists. Cost: every boot is delayed by the 3-second window unless
   interrupted by a press.

**Config storage** (`config_store.py`):
- A single JSON file on the Pico's internal flash filesystem (e.g.
  `/config.json`), containing: Wi-Fi SSID/password, chosen downstream backend
  + its credentials, and the sensor-to-GPIO-pin map.
- Writes are atomic (write to a temp file, then rename) so a power loss
  mid-write can't corrupt the active config.
- `config_schema.py` defines the full expected shape and is used both to
  validate on save (setup flow) and on load (boot) — a corrupted or
  incompatible config on load falls back to SETUP mode rather than crashing.

**Setup form fields:**
- Wi-Fi: SSID, password
- Backend: a select of `local_mqtt` / `home_assistant` (broker host/port/
  username/password) plus a three-way choice of connection security:
  verified TLS (default; needs a CA installed from a PC over USB),
  encrypted but unverified, or plaintext. `azure_iot_hub` is deferred and
  neither offered nor accepted until its adapter exists.
- Sensors: for each v1 sensor type, a GPIO pin selector (or "not connected")

## 6. Device Runtime & Sensor Adapter Interface

`runtime/device.py` owns the main loop once provisioning has handed off:

```python
class Device:
    def __init__(self, config):
        self.sensors = load_sensor_adapters(config["sensors"])    # pin map -> adapter instances
        self.adapter = load_downstream_adapter(config["backend"])  # one DownstreamAdapter instance

    def run(self):
        wifi.connect(config["wifi"]["ssid"], config["wifi"]["password"])
        self.adapter.connect()
        self.adapter.announce(self.sensors)
        while True:
            for sensor in self.sensors:
                reading = sensor.read()    # dict: one key per measurement
                self.adapter.publish(sensor.topic, reading)
            command = self.adapter.poll_commands()
            if command:
                self._dispatch(command)
            sleep(config["poll_interval_s"])
```

`sensors/base.py` — `SensorAdapter` abstract base every sensor driver implements:

```python
class SensorAdapter:
    def __init__(self, name, **options):
        # options are bus/pin settings: `pin` for GPIO sensors,
        # `sda`/`scl`/`i2c_id`/`address` for I2C sensors
        ...

    def read(self):
        """Return a dict with one key per measurement, e.g.
        {"temperature_c": 21.4, "humidity_pct": 40.2}"""
        raise NotImplementedError

    @property
    def topic(self):
        """Base topic/attribute name used by downstream adapters, e.g. the sensor's name"""
        raise NotImplementedError

    def discovery_payload(self):
        """List of Home Assistant MQTT discovery configs, one per key of read()"""
        raise NotImplementedError
```

**V1 sensor types** (`sensors/dht11.py`, `sensors/aht10.py`,
`sensors/binary_input.py`):
- **DHT11** (single-GPIO temperature + humidity) — very cheap, uses
  MicroPython's built-in `dht` module; the simplest multi-value example
- **AHT10** (I2C temperature + humidity) — first I2C sensor; establishes how
  bus settings (`sda`, `scl`, `i2c_id`, `address`) appear in config
- **Binary input** (switch / PIR / reed switch — any simple digital GPIO-in
  device) — covers the whole class of on/off sensors with one driver

Adding a third sensor type means adding one new file implementing
`SensorAdapter` and registering it in a lookup table — no other file changes.

## 7. Downstream Adapter Interface

`adapters/base.py` — `DownstreamAdapter` abstract base every backend implements:

```python
class DownstreamAdapter:
    def __init__(self, config):
        ...

    def connect(self):
        raise NotImplementedError

    def publish(self, topic, reading):
        raise NotImplementedError

    def poll_commands(self):
        """Return a pending command dict, or None"""
        raise NotImplementedError

    def announce(self, sensors):
        """Optional: send discovery/registration metadata (e.g. HA discovery) for the given sensors"""
        pass
```

**V1 implementations:**

- **`local_mqtt.py`** — the base case. Connects to a user-specified broker
  (host, port, optional username/password), publishes sensor readings to
  `<device_id>/<sensor_topic>`, subscribes to `<device_id>/command` for
  incoming commands. TLS on by default when the broker offers it.
- **`home_assistant.py`** — composes `local_mqtt.py` rather than duplicating
  transport logic: same MQTT connection, plus an `announce()` that publishes
  Home Assistant's MQTT discovery payloads so each sensor auto-registers as
  an HA entity.
- **`azure_iot_hub.py`** (deferred to a later sprint; stub only, not registered) — materially different: authenticates with a device
  connection string (parsed into hostname/device ID/shared access key),
  generates a time-limited SAS token, connects over MQTT to IoT Hub's
  specific endpoint and topic structure (`devices/{device_id}/messages/events/`
  to publish, `devices/{device_id}/messages/devicebound/#` to receive), and
  re-generates the SAS token on expiry.

**Adding a new backend** (e.g. AWS IoT, documented as the first post-v1
extension) means one new file implementing `DownstreamAdapter`, registered in
the same lookup table pattern as sensors — the Application Runtime and the
setup form's backend list are the only other places that need a one-line
addition.

## 8. Security Requirements

V1 must-haves:

- **Password-protected setup AP** — the first-boot access point is never
  open; it uses a documented default WPA2 password. (Future option: let the
  user change this via config.)
- **TLS by default** — `local_mqtt.py` and `home_assistant.py` default to
  *verified* TLS: the broker's certificate must chain to a CA installed on the
  device and match the address it connects to. Two weaker modes are explicit
  opt-ins on the setup form: *encrypted, not verified* (stops passive
  sniffing, not an active impersonator) and *plaintext*. A verified
  connection never falls back to a weaker mode.
- **BOOTSEL-5s physical reset** — re-entering setup mode requires physical
  access to the board (holding BOOTSEL just after power-up), not a network-reachable
  endpoint, so a remote attacker can't force a device back into open setup
  mode.
- **Config validation on load** — a malformed or tampered config file falls
  back to SETUP mode rather than running with partial/invalid settings.

Explicitly deferred (revisit when Pico 2 / RP2350 becomes the primary target):

- **Encrypted-at-rest secrets** — Wi-Fi and backend credentials are currently
  stored as plaintext JSON on flash. The RP2040 (Pico 1) lacks a secure
  element or OTP-backed key storage; the RP2350 (Pico 2) has primitives
  (e.g. the approach used by `pico-kvstore`, deriving an encryption key from
  a device-unique chip ID) that would make this practical.
  `config_store.py` should be written with a swappable storage backend so
  this can be added later without changing its calling interface.
- **OTA update integrity** — not applicable yet since OTA itself is
  deferred; when it ships, firmware signature verification must ship with it.

## 9. Build Process

The end-user artifact is a single `.uf2` file with the entire application
frozen into the MicroPython firmware image — not a generic MicroPython build
plus copied `.py` files.

**Pipeline** (`build/build.sh`, run in the Docker environment under
`build/docker/` to pin exact MicroPython + Pico SDK versions across
contributors' machines and CI):

1. Clone the pinned MicroPython source and the Pico SDK (as submodules or
   pinned versions in the build container).
2. Copy `firmware/` into MicroPython's frozen-modules manifest location, per
   `build/manifest.py` (`build/stage.py` stages the files first, so the whole package is frozen).
3. Run MicroPython's standard `mpy-cross` + board build for the
   `rpi_pico_w` board, producing `pico-iot-kit-picow-vX.Y.uf2`. The board
   definition is derived from the stock one with Bluetooth switched off
   (`build/make_board.py`): nothing here uses it, and the Bluetooth stack
   (BTstack) is licensed for non-commercial use only. A `rpi_pico2_w` image is
   not built until that board has been tested.
4. The same step writes `THIRD_PARTY_LICENSES.txt` (`build/licenses.py`) from
   the pinned source tree, and fails if the image contains a component with no
   licence entry.
5. CI (`release.yml`) runs this on every tagged release and attaches the
   `.uf2`, a checksum file and `THIRD_PARTY_LICENSES.txt` to the GitHub
   Release — this is the file the end user downloads in step 1 of the
   end-user experience above.

Contributors working on the Python source don't need to run the full
freeze/build cycle for every change — a faster inner-loop (e.g. copying
`.py` files to a dev board via `mpremote` for quick iteration) should be
documented, reserving the frozen `.uf2` build for releases and final
verification.

## 10. V1 Scope and Roadmap

| Area | V1 | Deferred |
| --- | --- | --- |
| Boards | Pico W | Pico 2 W (planned, untested) |
| Sensors | DHT11, AHT10 (temperature + humidity), binary digital input (switch/PIR/reed) | BME680 and additional types via the same `SensorAdapter` interface — community-contributed |
| Backends | Local MQTT, Home Assistant (MQTT discovery) | Azure IoT Hub, AWS IoT, Google Cloud IoT, other clouds |
| Config | Web form over setup AP + captive portal, JSON on flash | — |
| Reset | BOOTSEL held 5s within 3s of boot | — |
| Security | Password-protected setup AP, TLS by default, physical reset | Encrypted-at-rest secrets (needs Pico 2 / RP2350 primitives), signed OTA |
| Updates | Re-flash over USB | OTA over Wi-Fi |

A device running v1 firmware should be a complete, usable product on its own
— every row in the "Deferred" column is an additive, backward-compatible
enhancement, not something the v1 architecture needs to be reworked to
support.

## 11. Repo Metadata & Implementation Checklist

- **Name:** `pico-iot-kit`
- **License:** MIT
- **Primary language:** MicroPython (Python 3-subset), frozen into a custom `.uf2`
- **Target hardware:** Raspberry Pi Pico W (the only board tested and released); Pico 2 W is planned, with the same codebase, once a board is available

**Ordered checklist:**

1. [x] Scaffold the directory structure; add `LICENSE` (MIT) and a `README.md`.
2. [x] Implement `sensors/base.py` (`SensorAdapter`) and `adapters/base.py`
   (`DownstreamAdapter`) — stubbed in this scaffold, flesh out as needed.
3. [x] Implement `config_schema.py` and `config_store.py`, with unit tests
   under `tests/test_provisioning/` using a mocked flash filesystem.
4. [x] Implement `ap_portal.py` (AP mode + captive portal + setup form) and
   `manager.py` (state machine + BOOTSEL watch).
5. [x] Implement `runtime/device.py` main loop.
6. [x] Implement the three v1 sensor adapters (`dht11.py`, `aht10.py`,
   `binary_input.py`).
7. [x] Implement the v1 downstream adapters (`local_mqtt.py`,
   `home_assistant.py`), with TLS-by-default. (`azure_iot_hub.py` is deferred.)
8. [x] Wire `main.py` (via `app.py`) to call provisioning then runtime, including
   the BOOTSEL-5s check and crash recovery. There is no `boot.py`: MicroPython
   runs it itself, so a `main.py` doing `import boot` ran it twice (confirmed on
   a Pico W). Re-verified on a frozen image in Sprint 4 (4c).
9. [x] Set up `build/docker/`, `manifest.py`, and `build.sh` to produce
   a frozen `.uf2`; verify by flashing a real Pico W and completing the full
   setup flow end-to-end.
10. [x] Add `.github/workflows/ci.yml` (lint + host-side tests) and
    `release.yml` (build + attach `.uf2` on tag push). The tag-push publish job has
    not yet run; the build job has, from a manual start.
11. [x] Write `docs/adding-a-sensor.md` and `docs/adding-a-backend.md` as
    contributor guides referencing the interfaces above.
12. [x] Write `docs/security.md` capturing the security section in full,
    including the deferred items and why.

Each checklist item should land as its own commit or small PR so the
provisioning/runtime/adapter separation stays visible in the repo's history —
useful both for review and as a portfolio artifact in its own right.
