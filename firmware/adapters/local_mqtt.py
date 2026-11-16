"""Local MQTT broker adapter -- the base case all other MQTT-based
adapters (home_assistant.py) build on.

Checklist item 7 (docs/architecture.md, section 11). Connection security is
one of three explicit modes (firmware/adapters/tls.py); verified TLS is the
default and the weaker modes are opt-ins.

Topics (all under the hardware-derived device id):
    <device_id>/<sensor.topic>   one JSON message per sensor per poll (QoS 0,
                                 not retained)
    <device_id>/status           "online" (retained) once connected; the
                                 broker publishes the retained last will
                                 "offline" if the connection dies
    <device_id>/command          subscribed; JSON object commands

Details that come from reading umqtt.simple, which this module is careful
about:
- connect() has no default timeout and check_msg() resets the socket to
  fully blocking, so a dead broker could freeze the device; the socket
  timeout is (re)applied after connecting and after every check.
- usernames and passwords are passed as bytes, because the client measures
  them with len() (wrong for non-ASCII text), and an empty password is sent
  when only a username is set (None would crash it).
- a CONNACK error is just an int, so it is translated to a sentence; -1
  usually means a TLS/plaintext or port mismatch.

Commands are untrusted input: bounded in size and count, must be a JSON
object, and are only returned to the runtime, never executed here. The
client does not expose MQTT's retain flag, so a retained command would be
redelivered on every reconnect; v1 has no actuators, but a future one must
deduplicate.
"""

import json

from firmware.runtime import clock

from . import tls
from .base import DownstreamAdapter

CONNECT_TIMEOUT_S = 10
IO_TIMEOUT_S = 10
MIN_KEEPALIVE_S = 60
MAX_COMMAND_BYTES = 1024
MAX_PENDING_COMMANDS = 5
DRAIN_LIMIT = 4  # check_msg() handles one packet per call

_CONNACK_ERRORS = {
    1: "the broker does not support MQTT 3.1.1",
    2: "the broker rejected the client id",
    3: "the broker is unavailable",
    4: "the broker rejected the username or password",
    5: "the broker says this device is not authorized",
    128: "the broker refused the subscription to the command topic",
    -1: (
        "the broker did not answer like an MQTT broker; check the port and "
        "whether the security mode (TLS or plaintext) matches the broker"
    ),
}


# Network error numbers as MicroPython reports them (same values as Linux).
_OS_ERRORS = {
    -2: "could not find the broker's address; check the host name",
    -1: "the broker closed the connection",
    101: "the network is unreachable",
    104: "the connection was reset (refused or closed by the broker or a firewall)",
    110: "the broker did not answer in time",
    111: "nothing is listening on that port",
    113: "there is no route to the broker",
}


class MqttConnectError(Exception):
    pass


def _describe_os_error(exc):
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if code in _OS_ERRORS:
        return _OS_ERRORS[code]
    if code is not None and code < -1000:  # mbedTLS error codes are large negatives
        return "the TLS handshake failed (code %d); check the security mode and the port" % code
    return "a network error occurred (code %s)" % code


def _describe_certificate_error(exc):
    text = str(exc.args[0]) if exc.args else "unknown problem"
    return " ".join(text.split()) or "unknown problem"


def _discard(client):
    """Close a client's socket, ignoring errors (it may be half-open or dead)."""
    sock = getattr(client, "sock", None)
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass


def _check_segment(value, what):
    """Topic segments come from validated names, but never trust that here."""
    if not value or "/" in value or "+" in value or "#" in value:
        raise ValueError("invalid MQTT topic segment for %s" % what)


class LocalMqttAdapter(DownstreamAdapter):
    """Publishes to / subscribes from a user-specified MQTT broker."""

    def __init__(self, config):
        super().__init__(config)
        device_id = config.get("device_id")
        if not device_id:
            raise ValueError("backend config needs a device_id (the runtime adds it)")
        _check_segment(device_id, "device_id")
        self.device_id = device_id
        self.host = config["host"]
        self.mode = tls.mode_for(config)
        default_port = 1883 if self.mode == tls.PLAINTEXT else 8883
        self.port = config.get("port") or default_port
        self.username = config.get("username") or ""
        self.password = config.get("password") or ""
        poll_interval = config.get("poll_interval_s") or 0
        # a ping goes out every poll, so the broker's keepalive window must
        # comfortably exceed the poll interval
        self.keepalive_s = max(MIN_KEEPALIVE_S, int(2 * poll_interval))
        self.status_topic = "%s/status" % device_id
        self.command_topic = "%s/command" % device_id
        self._client = None
        self._pending = []

    # -- connection ---------------------------------------------------------

    def connect(self):
        self._close()
        context = tls.make_context(self.mode)  # fails fast without a usable CA
        if tls.needs_clock(self.mode):
            clock.sync()
        print("mqtt: connecting to %s:%d (%s)" % (self.host, self.port, tls.LABELS[self.mode]))

        from umqtt.simple import MQTTClient, MQTTException

        client = MQTTClient(
            self.device_id.encode(),
            self.host,
            port=self.port,
            user=self.username.encode() or None,
            password=self.password.encode(),
            keepalive=self.keepalive_s,
            ssl=context,
        )
        client.set_callback(self._on_message)
        client.set_last_will(self.status_topic.encode(), b"offline", retain=True)
        failure = None
        try:
            client.connect(timeout=CONNECT_TIMEOUT_S)
            self._client = client
            self._arm_timeout()
            client.subscribe(self.command_topic.encode())
            self._arm_timeout()
            client.publish(self.status_topic.encode(), b"online", retain=True)
        except MQTTException as exc:
            code = exc.args[0] if exc.args else -1
            failure = "MQTT connect failed: %s." % _CONNACK_ERRORS.get(code, "error %s" % code)
        except OSError as exc:
            failure = "MQTT connect failed: %s." % _describe_os_error(exc)
        except ValueError as exc:  # MicroPython raises these for certificate problems
            failure = "MQTT connect failed: the broker's certificate was not accepted (%s)." % (
                _describe_certificate_error(exc)
            )
        except BaseException:
            _discard(client)
            self._client = None
            raise
        if failure is not None:
            _discard(client)  # connect() may have opened the socket before failing
            self._client = None
            raise MqttConnectError(failure)

    def _arm_timeout(self):
        sock = self._client.sock
        try:
            sock.settimeout(IO_TIMEOUT_S)
        except AttributeError:  # a socket type without settimeout
            pass

    def _close(self):
        client, self._client = self._client, None
        self._pending = []
        if client is not None:
            _discard(client)

    # -- the DownstreamAdapter interface ------------------------------------

    def publish(self, topic, reading):
        _check_segment(topic, "sensor topic")
        self.publish_raw("%s/%s" % (self.device_id, topic), json.dumps(reading).encode())

    def publish_raw(self, topic, payload, retain=False):
        """Publish `payload` (bytes) on a full topic string.

        For protocol-level messages such as Home Assistant discovery that
        don't live under the device's own topic. Wildcards are never valid
        in a publish topic.
        """
        if not topic or "+" in topic or "#" in topic:
            raise ValueError("invalid MQTT publish topic")
        if self._client is None:
            raise OSError("MQTT is not connected")
        self._client.publish(topic.encode(), payload, retain=retain)

    def poll_commands(self):
        if self._client is None:
            raise OSError("MQTT is not connected")
        self._client.ping()  # keeps the session alive; a dead socket raises here
        for _ in range(DRAIN_LIMIT):
            self._client.check_msg()
            self._arm_timeout()  # check_msg() leaves the socket fully blocking
        if self._pending:
            return self._pending.pop(0)
        return None

    # -- incoming messages --------------------------------------------------

    def _on_message(self, topic, msg):
        """Called by the client for each message; must never raise."""
        try:
            if topic != self.command_topic.encode():
                return
            if len(msg) > MAX_COMMAND_BYTES:
                print("mqtt: command ignored (too large)")
                return
            command = json.loads(msg)
            if not isinstance(command, dict):
                print("mqtt: command ignored (not a JSON object)")
                return
            if len(self._pending) >= MAX_PENDING_COMMANDS:
                print("mqtt: command ignored (queue full)")
                return
            self._pending.append(command)
        except Exception:
            print("mqtt: command ignored (not valid JSON)")
