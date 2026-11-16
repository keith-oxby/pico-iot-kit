# Security model

## V1 must-haves

- **Password-protected setup AP.** The first-boot access point
  (`firmware/provisioning/ap_portal.py`) is never open; it uses a
  documented default WPA2 password (`DEFAULT_AP_PASSWORD`). A future
  revision may let the user change this via config, or randomize it
  per-device and print/display it somewhere discoverable at first boot.
- **TLS by default.** `local_mqtt.py` and `home_assistant.py` default to
  *verified* TLS: the broker's certificate must chain to the CA in `/ca.pem`
  (installed from a PC over USB) and match the address the device connects
  to; the clock is set over NTP first because certificate dates are checked.
  Two weaker modes are explicit opt-ins on the setup form: *encrypted, not
  verified* (stops passive sniffing but not an active impersonator) and
  *plaintext*. A verified connection never silently falls back to a weaker
  mode: with no usable CA it refuses to connect and logs why. A file holding
  a private key is never accepted as the CA. The trade-offs are written up
  under "MQTT connection security" below.
- **BOOTSEL-5s physical reset.** Re-entering setup mode
  (`firmware/provisioning/manager.py`) requires physically holding the
  BOOTSEL button just after power-up (the RP2040 bootloader claims the
  button if it is held at reset), not a network-reachable endpoint -- a
  remote attacker on the same network can't force a device back into open
  setup mode.
- **Config validation on load.** A malformed or tampered `config.json`
  fails `config_schema.validate()` on load and the device falls back to
  SETUP mode rather than running with partial or invalid settings.

## MQTT connection security

The setup page offers three modes. Verified TLS is the default and the only
one that protects against someone on your network pretending to be the
broker.

| Mode | Stops a passive listener | Stops an impersonating broker | Your broker password is exposed to |
|---|---|---|---|
| **Verified TLS** (default) | yes | yes | nobody on the network |
| **Encrypted, not verified** | yes | **no** | an active impersonator on your network |
| **Plaintext** | **no** | **no** | any listener on your network |

Choose verified unless you cannot install a CA certificate. Unverified is
for a LAN where you can't set one up; plaintext is for a network you fully
trust. If you use either, use a broker password that you use nowhere else.

### How verified mode works, and what it relies on

- The CA certificate is installed from a PC over USB (`tools/picokit.py`),
  never through the setup page or any network endpoint, so nobody on the
  network can plant or replace it. It lives in `/ca.pem`, outside
  `config.json`: it is public data, and a BOOTSEL reset keeps it (checked on
  the board).
- The broker's certificate must chain to that CA **and** contain the exact
  host name or IP address the device connects to. A certificate from the
  right CA but for another address is refused (checked on the board: "the
  Common Name does not match").
- **Whoever holds the CA's private key can impersonate your broker.** Keep
  `ca.key` on your PC, never copy it to the board (a file with a private key
  is refused as a CA), and keep a backup somewhere safe.
- **No downgrade.** If the CA is missing, unreadable or wrong, the device
  refuses to connect and logs why. It never retries in a weaker mode.
- **The clock comes from NTP, which is not authenticated.** Verified mode
  needs the date to check certificate validity, so someone who can spoof NTP
  on your network could make the device accept an expired certificate (or
  stop it connecting at all). The window is small, and an attacker still
  needs a certificate your CA once signed, but it is a real limit. If NTP is
  unreachable, verified mode fails closed. Use a CA with a long life and
  short-lived broker certificates if this matters to you.

### What the broker must do

- The device trusts whatever the broker tells it. Turn off anonymous access
  (`allow_anonymous false` with a password file) and, for anything beyond
  sensors, add per-device topic permissions. The test broker in
  `tools/test-broker/` is anonymous on purpose and must never be exposed.
- The device subscribes to `<device_id>/command`, and anyone who can publish
  there can send it a command. v1 has no actuators, so commands are parsed
  (at most 1 KiB, at most 5 queued, JSON objects only) and then ignored.
  Before an actuator is added, the broker must restrict who can publish to
  that topic, and the device must deduplicate retained commands.

### Reviewed in Sprint 3 and found sound

- **Secrets in logs.** Nothing logs `config.json`, passwords or the CA. Log
  lines carry the host, port, mode and an error class or sentence. Network
  errors are reported as code numbers.
- **Topic building.** Topics use only the hardware-derived `device_id` and
  names the schema limits to letters, digits, `-` and `_`. Every segment is
  re-checked for `/`, `+` and `#` at the point of use, and publish topics
  with wildcards are refused.
- **Home Assistant cleanup.** Only the exact topic
  `homeassistant/<component>/<device_id>/<object>/config` is ever cleared,
  whatever `/ha_announced.json` contains. (A review finding: the first
  version only checked that the device id appeared somewhere in the topic.)
- **Untrusted input.** Incoming commands are bounded and never executed.
  The vendored MQTT client is pinned to a hash-checked copy.

### Known limits of the connection layer

- Credentials and the CA sit on flash in the clear (see the deferred list
  below); physical access to the board reveals them.
- In unverified mode, nothing tells the broker apart from an impersonator,
  by design.
- The MQTT username and password are sent after the TLS handshake. In
  plaintext mode they are sent as they are.

## Explicitly deferred

- **Encrypted-at-rest secrets.** Wi-Fi and backend credentials are
  currently stored as plaintext JSON on flash
  (`firmware/provisioning/config_store.py`). The RP2040 (Pico 1, the
  primary v1 target) lacks a secure element or OTP-backed key storage. The
  RP2350 (Pico 2) has primitives that would make this practical -- see the
  `pico-kvstore` project's approach of deriving an encryption key from a
  device-unique chip ID. `config_store.py`'s interface (`exists` / `load`
  / `save` / `clear`) is deliberately narrow so an encrypted backend can be
  swapped in later without changing any caller.
- **OTA update integrity.** Not applicable yet since OTA itself is
  deferred (re-flashing over USB is the v1 update path). When OTA ships,
  firmware signature verification must ship with it, not follow later.

## Availability

- **Hardware watchdog.** The firmware arms the RP2040 watchdog at boot
  (`firmware/watchdog.py`), so a hung program resets the board.
- **Stall reset.** `Device` restarts the board after 15 minutes without a
  successful connection, to recover a wedged Wi-Fi chip. No flash is
  written. A broker or router that is down for a long time therefore causes
  a reboot every 15 minutes, never a crash loop into a weaker mode.

## Setup-mode caveats

- **The default AP password is public.** Anyone in radio range who has read
  the README can join the setup AP and, as another WPA2-PSK client, could
  observe or contend with the setup session, which carries the home Wi-Fi
  password and backend credentials over plain HTTP inside that link. The
  window is limited to first boot or a deliberate BOOTSEL reset. Mitigation
  options for a later revision: a per-device random password shown on a
  label or the serial console, or requiring BOOTSEL confirmation before the
  portal accepts a submission.
- **Portal surface.** The portal serves exactly one page and one form POST;
  every other GET redirects to the page. It exposes no endpoint that resets
  the device, clears config or re-enters setup mode, rejects bodies over
  4 KiB, handles one client at a time with a 5-second socket timeout,
  HTML-escapes anything it reflects, and never logs submitted values.
- **DNS.** In setup mode a minimal DNS responder answers every query with
  the AP address so phones detect the captive portal. It only runs while
  the AP is up.

## Threat model notes

This is a hobbyist/prosumer IoT platform, not a hardened commercial
product. The security bar aimed for is: don't leak Wi-Fi/broker
credentials to a casual bystander during setup, don't allow a
network-only attacker to force a device back into an insecure state, and
don't ship with plaintext transport as the silent default. It explicitly
does not aim for resistance against an attacker with physical possession
of the board (flash can be read off-chip on the RP2040 regardless of what
this firmware does) -- that's the gap the Pico 2 / RP2350 path above is
meant to close later.
