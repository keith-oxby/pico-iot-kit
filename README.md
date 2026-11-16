# pico-iot-kit

An open-source MicroPython IoT platform for the Raspberry Pi Pico W (Pico 2 W is planned but not yet tested):
**flash once, configure entirely from a phone or PC over Wi-Fi — no code editing required.**

## Status

Version 0.1.0. The firmware runs end to end on a Pico W: set up from a phone,
verified TLS to a private MQTT broker, Home Assistant discovery, DHT11, AHT10
and binary-input sensors, and a watchdog that recovers a stuck board. A frozen
`.uf2` for the Pico W is built in CI and attached to each
[release](../../releases). See
[the full project specification](docs/architecture.md) for the architecture,
security model and checklist.

## What this is

Most Pico IoT projects ask you to write and copy MicroPython files for every
new device. pico-iot-kit flips that: you download one pre-built firmware
image, flash it, and configure everything else — Wi-Fi, sensors, and which
backend to talk to — through a web form served by the device itself.

- **Flash once.** A single `.uf2` image with the whole application frozen in.
- **Configure over Wi-Fi.** First boot starts a password-protected setup
  access point and a captive-portal web page — no USB file copying, no IDE.
- **Provisioning is separate from application logic.** A dedicated
  provisioning layer handles getting the device online; the application
  runtime only starts once that's done.
- **Pluggable backends.** Local MQTT and Home Assistant (via MQTT discovery)
  ship in v1, behind a common `DownstreamAdapter` interface. Azure IoT Hub is
  planned for a later release.
- **Pluggable sensors.** A `SensorAdapter` interface with v1 drivers for
  DHT11 and AHT10 temperature/humidity sensors and a generic binary digital
  input (switch/PIR/reed).

## Quick start (end user)

1. Download the latest `.uf2` for your board from
   [Releases](../../releases).
2. Hold BOOTSEL, plug the Pico into your computer via USB, drag the `.uf2`
   onto the drive that appears.
3. On first boot, connect to the `PicoIoTKit-XXXX` Wi-Fi network from your
   phone or PC (the password is `setupme123`, the same on every board) and follow the setup page to enter your Wi-Fi credentials,
   choose a backend, and assign sensors to pins. For an MQTT or Home
   Assistant backend, read [Connecting to your MQTT broker](#connecting-to-your-mqtt-broker)
   first: the default security mode needs a certificate installed from a PC
   before the device can connect. Keep your phone's screen on
   while you do this: many phones drop a Wi-Fi network without internet when
   they lock, and you would have to rejoin and start again.
4. The device reboots onto your network and starts running.
5. To reconfigure later, power the board up and, while its LED is on (the first
   3 seconds), press and hold BOOTSEL for 5 seconds. Don't hold it while plugging
   in: that starts the USB bootloader instead. The LED blinks three times when
   the reset is accepted; let go then, and the setup network will appear.

## Connecting to your MQTT broker

The setup form offers three ways to secure the connection to your broker
(Mosquitto, Home Assistant's MQTT add-on, or any other MQTT 3.1.1 broker):

| Mode | Protects against | Needs |
|---|---|---|
| **Verified TLS** (default, recommended) | listeners and impersonators | a CA certificate on the board, installed from a PC over USB |
| **Encrypted, not verified** | listeners only | nothing; a broker with TLS enabled |
| **Plaintext** | nothing | nothing; only for a network you fully trust |

The trade-offs, including what each mode exposes (your broker password
included), are in [docs/security.md](docs/security.md). Pick **Verified TLS**
unless you can't.

### Verified TLS with your own CA (about ten minutes)

You need a PC with Python 3 and the board connected over USB. The
device never accepts a certificate over the network, which is why this step
happens on a PC.

1. **Install the helper's dependencies** (once):

   ```
   pip install -r tools/requirements.txt
   ```

2. **Make a CA and a broker certificate.** Give every address the device
   might use to reach the broker, as an IP address, a host name, or both:

   ```
   python tools/picokit.py make-certs --ip 192.168.0.10 --host mqtt.home.lan
   ```

   This writes `pico-iot-kit-certs/` containing `ca.crt`, `broker.crt`,
   `broker.key`, `ca.key` and a ready-to-paste `mosquitto-tls.conf`. The
   certificate is for the board to check, so **the host you type on the setup
   form must be exactly one of the names you gave here**. To add an address
   later, run the command again with `--force` and give **all** the
   addresses again; the CA is reused, so the board needs nothing new, but
   the broker needs the new `broker.crt` and `broker.key`.

   Keep `ca.key` on your PC and somewhere safe. Anyone who has it can make
   certificates your board will trust. Never copy it to the board.

3. **Configure the broker.** Copy `broker.crt` and `broker.key` to the
   machine running Mosquitto (readable by the Mosquitto user) and add the
   settings from `mosquitto-tls.conf`, adjusting the paths:

   ```
   listener 8883
   certfile /etc/mosquitto/certs/broker.crt
   keyfile  /etc/mosquitto/certs/broker.key
   ```

   Also turn off anonymous access (`allow_anonymous false` and a password
   file), then restart the broker.

4. **Install the CA on the board**, connected by USB:

   ```
   python tools/picokit.py install-ca pico-iot-kit-certs/ca.crt
   ```

   The tool checks that the file is a certificate (it refuses a private
   key), copies it to the board, confirms the copy, shows the fingerprint,
   and restarts the board. `python tools/picokit.py status` shows which CA
   is installed, and `remove-ca` removes it.

5. **Set up the device** (BOOTSEL hold, as above): choose your backend, enter
   the broker's host as above, leave the security choice on **Verified
   TLS**, and add the broker username and password. The port defaults to
   8883 (TLS) or 1883 (plaintext).

The CA stays on the board when you reset the setup with BOOTSEL, so you only
install it once.

Verified TLS also needs the right date, so the device asks an NTP server
(over the internet) for the time before it connects. On a network with no
internet access, use *Encrypted, not verified* or *Plaintext*.

### Using a public certificate (for example Let's Encrypt)

If your broker has a real host name and a certificate from a public CA, the
device can trust that CA's root instead. Install the root certificate the
chain ends in with `install-ca` (it accepts a file holding several
certificates), point Mosquitto's `certfile` at the full chain, and enter the
broker's host name on the setup form. The name must resolve from your Wi-Fi
network, and it can't be an IP address unless the certificate lists it.
Renewing the broker certificate then needs nothing on the board, because it
only trusts the root. This route is not tested on a board yet.

### If it doesn't connect

The board prints what it is doing on its USB serial port. The messages that
matter:

| Message | What to do |
|---|---|
| `No CA certificate is installed on the device` | Run `install-ca` (step 4), or choose another security mode. The device never falls back to a weaker mode by itself. |
| `the broker's certificate was not accepted (…not correctly signed by the trusted CA)` | The board has a different CA than the one that signed the broker's certificate. Check with `status`, and install the right `ca.crt`. |
| `the broker's certificate was not accepted (…Common Name (CN) does not match…)` | The host on the setup form isn't in the broker's certificate. Re-run `make-certs` with that address and `--force`, restart the broker, or fix the host. |
| `the broker rejected the username or password` | The credentials entered on the setup form are wrong. Repeat the setup. |
| `the broker did not answer like an MQTT broker` | The security mode and the port don't match (TLS on a plaintext port, or the reverse). |

The device keeps trying, with growing pauses of up to a minute or so, so it
recovers by itself once the cause is fixed.

## Repository layout

See [docs/architecture.md](docs/architecture.md) for the full breakdown of
`firmware/provisioning`, `firmware/runtime`, `firmware/sensors`, and
`firmware/adapters`.

## Contributing

Adding a sensor or a backend should only require one new file behind the
relevant interface. See [docs/adding-a-sensor.md](docs/adding-a-sensor.md) and
[docs/adding-a-backend.md](docs/adding-a-backend.md).

## License

MIT — see [LICENSE](LICENSE). A released `.uf2` also contains third-party
code under its own licences, listed in `THIRD_PARTY_LICENSES.txt` (attached to
each release); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
