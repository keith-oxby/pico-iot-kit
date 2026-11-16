"""Application Runtime: the main loop that runs once provisioning has
handed off a validated config.

Knows nothing about *how* a sensor is read or *how* a backend talks --
only that both conform to their interfaces (firmware/sensors/base.py,
firmware/adapters/base.py). Checklist item 5 (docs/architecture.md,
section 11).

Failure policy:
- A sensor whose `read()` raises is skipped for that cycle; the others are
  still published and the loop carries on.
- A failure in `publish()` or `poll_commands()` is treated as a lost
  connection: the loop rejoins Wi-Fi, reconnects the backend and
  re-announces, then continues with the next cycle.
- Connecting (at startup or after a loss) retries forever, sleeping with an
  exponential backoff (BACKOFF_START_S doubling up to BACKOFF_MAX_S) that
  resets after each success.
- A failure in `_dispatch()` is logged and otherwise ignored.
- No success (a connection made or a cycle completed) for STALL_RESET_S
  restarts the device. The watchdog (firmware/watchdog.py) only catches a
  hung program; a healthy loop retrying against a wedged Wi-Fi chip checks in
  forever, so this is what recovers it. Checked on every retry.
Only `Exception` is caught, so Ctrl-C still stops the loop from the REPL.
"""


from firmware import watchdog
from firmware.adapters import load_downstream_adapter
from firmware.sensors import load_sensor_adapters

from . import wifi

DEFAULT_POLL_INTERVAL_S = 30
BACKOFF_START_S = 1
BACKOFF_MAX_S = 60
STALL_RESET_S = 15 * 60
HEAP_LOG_EVERY = 20  # cycles; the soak test watches this line for leaks


def device_id():
    """Stable per-board identifier: the full unique ID as lowercase hex."""
    import machine

    return "".join("%02x" % b for b in machine.unique_id())


def _machine_reset():
    import machine

    machine.reset()


def _log_heap():
    import gc

    if hasattr(gc, "mem_free"):  # MicroPython only
        gc.collect()
        _log("heap free %d bytes" % gc.mem_free())


def _log(message, exc=None):
    # Never log config contents: it holds Wi-Fi and backend credentials.
    if exc is None:
        print("device:", message)
    else:
        print("device:", message, "-", type(exc).__name__, exc)


class Device:
    """Owns the sensor-read / publish / command-handle loop."""

    def __init__(self, config, sleep=watchdog.nap, reset=_machine_reset, now_ms=watchdog.now_ms):
        self.config = config
        self._sleep = sleep
        self._reset = reset
        self._now_ms = now_ms
        self._last_success = now_ms()
        self.sensors = load_sensor_adapters(config["sensors"])
        # device_id comes from the hardware, never from config: it ends up
        # in MQTT topics, which the schema doesn't constrain for it.
        self.poll_interval_s = config.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S)
        backend_config = dict(
            config["backend"], device_id=device_id(), poll_interval_s=self.poll_interval_s
        )
        self.adapter = load_downstream_adapter(backend_config)

    def run(self, max_cycles=None):
        """Run the loop forever (or for `max_cycles` cycles, for tests)."""
        self._ensure_connected()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            watchdog.checkin()
            if cycles % HEAP_LOG_EVERY == 0:
                _log_heap()
            try:
                self._cycle()
                self._mark_success()
            except Exception as exc:
                _log("connection lost, reconnecting", exc)
                self._ensure_connected()
            cycles += 1
            self._sleep(self.poll_interval_s)

    def _cycle(self):
        for sensor in self.sensors:
            try:
                reading = sensor.read()
            except Exception as exc:
                _log("read failed for %s, skipping" % sensor.name, exc)
                continue
            self.adapter.publish(sensor.topic, reading)
        command = self.adapter.poll_commands()
        if command:
            try:
                self._dispatch(command)
            except Exception as exc:
                _log("command failed", exc)

    def _ensure_connected(self):
        """Join Wi-Fi, connect the backend and announce; retry until it works."""
        wifi_config = self.config["wifi"]
        backoff = BACKOFF_START_S
        while True:
            watchdog.checkin()
            try:
                wifi.connect(wifi_config["ssid"], wifi_config["password"])
                self.adapter.connect()
                self.adapter.announce(self.sensors)
                self._mark_success()
                return
            except Exception as exc:
                _log("connect failed, retrying in %ds" % backoff, exc)
                self._check_stalled()
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)

    def _mark_success(self):
        self._last_success = self._now_ms()

    def _check_stalled(self):
        if watchdog.ticks_diff(self._now_ms(), self._last_success) >= STALL_RESET_S * 1000:
            _log("no connection for %d minutes, restarting" % (STALL_RESET_S // 60))
            self._sleep(1)  # reset drops what is still in the USB buffer; let the line out first
            self._reset()

    def _dispatch(self, command):
        # TODO: route an incoming command dict to the relevant sensor/
        # actuator. v1 sensors are read-only, so this is a hook for
        # future actuator-type adapters.
        pass


def run(config):
    """Entry point called by firmware/main.py once config is confirmed present."""
    Device(config).run()
