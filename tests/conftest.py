"""Shared fixtures for host-side tests.

MicroPython hardware modules (machine, network, ...) don't exist on the
host. Tests that import code touching them request the `fake_hardware`
fixture, which injects MagicMock stand-ins into `sys.modules` for the
duration of the test and removes them afterwards.
"""

import sys
from unittest.mock import MagicMock

import pytest

HARDWARE_MODULES = (
    "machine",
    "network",
    "onewire",
    "ds18x20",
    "dht",
    "umqtt",
    "umqtt.simple",
    "usocket",
    "ubinascii",
    "uhashlib",
)


@pytest.fixture
def fake_hardware(monkeypatch):
    """Install fake MicroPython hardware modules; returns {name: MagicMock}.

    Configure behaviour per test, e.g.
    `fake_hardware["machine"].unique_id.return_value = b"\\xe6\\x61\\x64\\x12"`.
    """
    fakes = {name: MagicMock(name=name) for name in HARDWARE_MODULES}
    for name, fake in fakes.items():
        monkeypatch.setitem(sys.modules, name, fake)
    return fakes


class FakeMQTTException(Exception):
    pass


class FakeSock:
    def __init__(self):
        self.closed = False
        self.timeout = "unset"

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


class MqttController:
    """Scripts and records everything the fake umqtt.simple client does."""

    def __init__(self):
        self.clients = []
        self.events = []
        self.inbox = []          # (topic, msg) delivered one per check_msg()
        self.connect_error = None
        self.subscribe_error = None
        self.publish_error = None
        self.ping_error = None

    @property
    def client(self):
        return self.clients[-1]


def _make_fake_client_class(ctl):
    class FakeMQTTClient:
        def __init__(self, client_id, server, port=0, user=None, password=None,
                     keepalive=0, ssl=None, ssl_params=None):
            if port == 0:
                port = 8883 if ssl else 1883
            self.client_id, self.server, self.port = client_id, server, port
            self.user, self.password, self.keepalive, self.ssl = user, password, keepalive, ssl
            self.sock = None
            self.cb = None
            self.will = None
            self.published = []
            self.subscribed = []
            self.pings = 0
            self.connect_timeout = "unset"
            ctl.clients.append(self)
            ctl.events.append("init")

        def set_callback(self, f):
            self.cb = f
            ctl.events.append("set_callback")

        def set_last_will(self, topic, msg, retain=False, qos=0):
            self.will = (topic, msg, retain, qos)
            ctl.events.append("set_last_will")

        def connect(self, clean_session=True, timeout=None):
            self.sock = FakeSock()  # the real client opens the socket before it can fail
            self.sock.timeout = timeout
            self.connect_timeout = timeout
            ctl.events.append("connect")
            if ctl.connect_error is not None:
                raise ctl.connect_error

        def subscribe(self, topic, qos=0):
            ctl.events.append("subscribe")
            if ctl.subscribe_error is not None:
                raise ctl.subscribe_error
            self.subscribed.append(topic)
            self.sock.timeout = None  # wait_msg() leaves the socket blocking

        def publish(self, topic, msg, retain=False, qos=0):
            ctl.events.append("publish")
            if ctl.publish_error is not None:
                raise ctl.publish_error
            self.published.append((topic, msg, retain, qos))

        def ping(self):
            self.pings += 1
            if ctl.ping_error is not None:
                raise ctl.ping_error

        def check_msg(self):
            self.sock.timeout = None  # like the real one: setblocking(True) afterwards
            if ctl.inbox:
                topic, msg = ctl.inbox.pop(0)
                self.cb(topic, msg)

    return FakeMQTTClient


@pytest.fixture
def fake_mqtt(monkeypatch):
    """Install a fake `umqtt.simple`; returns an MqttController."""
    import types

    ctl = MqttController()
    simple = types.ModuleType("umqtt.simple")
    simple.MQTTClient = _make_fake_client_class(ctl)
    simple.MQTTException = FakeMQTTException
    package = types.ModuleType("umqtt")
    package.simple = simple
    monkeypatch.setitem(sys.modules, "umqtt", package)
    monkeypatch.setitem(sys.modules, "umqtt.simple", simple)
    ctl.exception = FakeMQTTException
    return ctl
