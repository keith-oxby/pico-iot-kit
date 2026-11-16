import json

import pytest

from firmware.adapters import local_mqtt, tls
from firmware.adapters.local_mqtt import LocalMqttAdapter, MqttConnectError
from firmware.runtime import ca_store, clock

DEVICE = "e6616412abcdef01"
CONFIG = {"host": "broker.local", "device_id": DEVICE}


@pytest.fixture
def rig(fake_mqtt, monkeypatch, tmp_path):
    """Adapter rig: fake client, recorded NTP syncs, a sentinel TLS context."""

    class Rig:
        mqtt = fake_mqtt
        syncs = []
        contexts = []

    rig = Rig()
    rig.syncs = []
    rig.contexts = []
    sentinel = object()

    def fake_context(mode):
        rig.contexts.append(mode)
        return None if mode == tls.PLAINTEXT else sentinel

    monkeypatch.setattr(local_mqtt.tls, "make_context", fake_context)
    monkeypatch.setattr(clock, "sync", lambda: rig.syncs.append(True))
    monkeypatch.setattr(ca_store, "CA_PATH", str(tmp_path / "ca.pem"))
    rig.sentinel = sentinel

    def make(**overrides):
        return LocalMqttAdapter(dict(CONFIG, **overrides))

    rig.make = make
    return rig


def connected(rig, **overrides):
    adapter = rig.make(**overrides)
    adapter.connect()
    return adapter


# --- construction -------------------------------------------------------------

def test_a_device_id_is_required_and_must_be_a_safe_topic_segment():
    with pytest.raises(ValueError, match="device_id"):
        LocalMqttAdapter({"host": "h"})
    for bad in ("a/b", "a#", "a+", ""):
        with pytest.raises(ValueError):
            LocalMqttAdapter({"host": "h", "device_id": bad})


@pytest.mark.parametrize("config,port", [
    ({}, 8883),
    ({"use_tls": True, "tls_verify": False}, 8883),
    ({"use_tls": False}, 1883),
    ({"port": 9001}, 9001),
    ({"use_tls": False, "port": 9001}, 9001),
])
def test_the_port_defaults_by_security_mode(rig, config, port):
    assert rig.make(**config).port == port


def test_topics_are_built_from_the_device_id(rig):
    adapter = rig.make()
    assert adapter.status_topic == DEVICE + "/status"
    assert adapter.command_topic == DEVICE + "/command"


@pytest.mark.parametrize("poll,keepalive", [(None, 60), (5, 60), (30, 60), (45, 90), (300, 600)])
def test_keepalive_always_exceeds_the_poll_interval(rig, poll, keepalive):
    overrides = {} if poll is None else {"poll_interval_s": poll}
    assert rig.make(**overrides).keepalive_s == keepalive


# --- connecting -----------------------------------------------------------------

def test_connect_sets_up_the_session_in_the_right_order(rig):
    connected(rig)
    assert rig.mqtt.events == [
        "init", "set_callback", "set_last_will", "connect", "subscribe", "publish",
    ]


def test_connect_uses_the_device_id_host_port_and_a_timeout(rig):
    connected(rig, port=8884)
    client = rig.mqtt.client
    assert client.client_id == DEVICE.encode()
    assert (client.server, client.port) == ("broker.local", 8884)
    assert client.connect_timeout == local_mqtt.CONNECT_TIMEOUT_S  # never block forever


def test_last_will_is_a_retained_offline_on_the_status_topic(rig):
    connected(rig)
    assert rig.mqtt.client.will == ((DEVICE + "/status").encode(), b"offline", True, 0)


def test_it_subscribes_to_commands_and_announces_online_retained(rig):
    connected(rig)
    client = rig.mqtt.client
    assert client.subscribed == [(DEVICE + "/command").encode()]
    assert client.published == [((DEVICE + "/status").encode(), b"online", True, 0)]


def test_the_io_timeout_is_armed_after_connecting(rig):
    connected(rig)
    assert rig.mqtt.client.sock.timeout == local_mqtt.IO_TIMEOUT_S


def test_credentials_are_passed_as_bytes(rig):
    connected(rig, username="bob", password="pässword")
    client = rig.mqtt.client
    assert client.user == b"bob"
    assert client.password == "pässword".encode()  # bytes, so len() matches the wire


def test_a_username_without_a_password_sends_an_empty_password_not_none(rig):
    connected(rig, username="bob")
    assert rig.mqtt.client.password == b""


def test_no_credentials_means_no_user(rig):
    connected(rig)
    assert rig.mqtt.client.user is None


# --- the three security modes ---------------------------------------------------

def test_verified_builds_a_context_and_syncs_the_clock_first(rig):
    connected(rig)
    assert rig.contexts == [tls.VERIFIED]
    assert rig.syncs == [True]
    assert rig.mqtt.client.ssl is rig.sentinel


def test_unverified_uses_tls_without_needing_the_clock(rig):
    connected(rig, tls_verify=False)
    assert rig.contexts == [tls.UNVERIFIED]
    assert rig.syncs == []
    assert rig.mqtt.client.ssl is rig.sentinel


def test_plaintext_has_no_tls_and_no_clock(rig):
    connected(rig, use_tls=False)
    assert rig.contexts == [tls.PLAINTEXT]
    assert rig.syncs == []
    assert rig.mqtt.client.ssl is None
    assert rig.mqtt.client.port == 1883


def test_the_context_is_built_before_waiting_for_ntp(rig, monkeypatch):
    order = []
    monkeypatch.setattr(local_mqtt.tls, "make_context", lambda m: order.append("context"))
    monkeypatch.setattr(clock, "sync", lambda: order.append("sync"))
    rig.make().connect()
    assert order == ["context", "sync"]  # a missing CA fails fast, not after an NTP wait


def test_verified_without_a_ca_refuses_before_creating_any_client(fake_mqtt, tmp_path, monkeypatch):
    monkeypatch.setattr(ca_store, "CA_PATH", str(tmp_path / "missing.pem"))
    adapter = LocalMqttAdapter(CONFIG)
    with pytest.raises(tls.TlsConfigError, match="No CA certificate"):
        adapter.connect()
    assert fake_mqtt.clients == []


def test_an_ntp_failure_is_a_connection_failure_not_a_crash(rig, monkeypatch):
    def fail():
        raise OSError("Could not set the clock over NTP.")

    monkeypatch.setattr(clock, "sync", fail)
    with pytest.raises(OSError, match="NTP"):
        rig.make().connect()
    assert rig.mqtt.clients == []


# --- failing to connect ----------------------------------------------------------

@pytest.mark.parametrize("code,fragment", [
    (4, "username or password"),
    (5, "not authorized"),
    (3, "unavailable"),
    (1, "MQTT 3.1.1"),
    (-1, "check the port"),
    (99, "error 99"),
])
def test_connack_codes_become_plain_sentences(rig, code, fragment):
    rig.mqtt.connect_error = rig.mqtt.exception(code)
    with pytest.raises(MqttConnectError, match=fragment) as exc:
        rig.make().connect()
    assert str(exc.value).startswith("MQTT connect failed:")
    assert str(exc.value).endswith(".")


def test_a_refused_connection_does_not_leak_the_socket(rig):
    rig.mqtt.connect_error = rig.mqtt.exception(5)
    with pytest.raises(MqttConnectError):
        rig.make().connect()
    assert rig.mqtt.client.sock.closed


@pytest.mark.parametrize("errno,fragment", [
    (-2, "check the host name"),
    (-1, "closed the connection"),
    (101, "network is unreachable"),
    (104, "connection was reset"),
    (110, "did not answer in time"),
    (111, "nothing is listening"),
    (113, "no route"),
    (-29312, "TLS handshake failed (code -29312)"),
    (77, "network error occurred (code 77)"),
])
def test_network_errors_while_connecting_become_plain_sentences(rig, errno, fragment):
    rig.mqtt.connect_error = OSError(errno)
    with pytest.raises(MqttConnectError, match="MQTT connect failed") as exc:
        rig.make().connect()
    assert fragment in str(exc.value) and str(exc.value).endswith(".")
    assert rig.mqtt.client.sock.closed


def test_certificate_problems_keep_their_detail_on_one_line(rig):
    rig.mqtt.connect_error = ValueError("\nThe certificate validity starts in the future\n")
    with pytest.raises(MqttConnectError) as exc:
        rig.make().connect()
    assert str(exc.value) == (
        "MQTT connect failed: the broker's certificate was not accepted "
        "(The certificate validity starts in the future)."
    )


def test_an_oserror_without_a_number_still_gives_a_sentence(rig):
    rig.mqtt.connect_error = OSError()
    with pytest.raises(MqttConnectError, match="network error occurred"):
        rig.make().connect()


def test_unexpected_errors_still_propagate_and_close_the_socket(rig):
    rig.mqtt.connect_error = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        rig.make().connect()
    assert rig.mqtt.client.sock.closed


def test_ctrl_c_during_connect_closes_the_socket(rig):
    rig.mqtt.connect_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        rig.make().connect()
    assert rig.mqtt.client.sock.closed


def test_a_refused_subscription_is_reported_and_cleaned_up(rig):
    rig.mqtt.subscribe_error = rig.mqtt.exception(128)
    with pytest.raises(MqttConnectError, match="subscription"):
        rig.make().connect()
    assert rig.mqtt.client.sock.closed


def test_reconnecting_closes_the_previous_connection_first(rig):
    adapter = connected(rig)
    first = rig.mqtt.client
    adapter.connect()
    assert first.sock.closed
    assert len(rig.mqtt.clients) == 2 and not rig.mqtt.client.sock.closed


def test_commands_queued_before_a_reconnect_are_dropped(rig):
    adapter = connected(rig)
    adapter._on_message((DEVICE + "/command").encode(), b'{"stale": 1}')
    assert adapter._pending == [{"stale": 1}]
    adapter.connect()
    assert adapter._pending == []


# --- publishing -------------------------------------------------------------------

def test_publish_sends_one_json_message_on_the_device_topic(rig):
    adapter = connected(rig)
    adapter.publish("lounge", {"temperature_c": 22, "humidity_pct": 46})
    topic, payload, retain, qos = rig.mqtt.client.published[-1]
    assert topic == (DEVICE + "/lounge").encode()
    assert json.loads(payload) == {"temperature_c": 22, "humidity_pct": 46}
    assert (retain, qos) == (False, 0)


def test_the_payload_is_bytes_so_length_matches_the_wire(rig):
    adapter = connected(rig)
    adapter.publish("door", {"state": "ON", "note": "café"})
    payload = rig.mqtt.client.published[-1][1]
    assert isinstance(payload, bytes)


@pytest.mark.parametrize("bad", ["", "a/b", "a+b", "a#", "../x"])
def test_unsafe_topics_are_refused(rig, bad):
    adapter = connected(rig)
    with pytest.raises(ValueError):
        adapter.publish(bad, {})
    assert len(rig.mqtt.client.published) == 1  # only the 'online' message


def test_publishing_while_disconnected_is_a_connection_error(rig):
    with pytest.raises(OSError, match="not connected"):
        rig.make().publish("lounge", {})


def test_a_socket_error_while_publishing_propagates_so_the_runtime_reconnects(rig):
    adapter = connected(rig)
    rig.mqtt.publish_error = OSError(104)
    with pytest.raises(OSError):
        adapter.publish("lounge", {"v": 1})


# --- commands ----------------------------------------------------------------------

def deliver(rig, payload, topic=None):
    rig.mqtt.inbox.append(((topic or DEVICE + "/command").encode(), payload))


def test_no_command_means_none_and_a_ping_keeps_the_session_alive(rig):
    adapter = connected(rig)
    assert adapter.poll_commands() is None
    assert rig.mqtt.client.pings == 1


def test_a_json_object_command_is_returned(rig):
    adapter = connected(rig)
    deliver(rig, b'{"action": "identify", "n": 3}')
    assert adapter.poll_commands() == {"action": "identify", "n": 3}
    assert adapter.poll_commands() is None


def test_commands_come_back_one_per_poll_in_order(rig):
    adapter = connected(rig)
    for n in (1, 2, 3):
        deliver(rig, json.dumps({"n": n}).encode())
    assert [adapter.poll_commands(), adapter.poll_commands(), adapter.poll_commands()] == [
        {"n": 1}, {"n": 2}, {"n": 3}]


@pytest.mark.parametrize("payload", [
    b"not json", b"", b"[1, 2]", b'"text"', b"42", b"null", b"{broken", b"\xff\xfe",
])
def test_anything_that_is_not_a_json_object_is_ignored(rig, payload):
    adapter = connected(rig)
    deliver(rig, payload)
    assert adapter.poll_commands() is None


def test_oversized_commands_are_ignored(rig):
    adapter = connected(rig)
    deliver(rig, b'{"x": "' + b"a" * local_mqtt.MAX_COMMAND_BYTES + b'"}')
    assert adapter.poll_commands() is None


def test_other_topics_are_ignored(rig):
    adapter = connected(rig)
    deliver(rig, b'{"a": 1}', topic=DEVICE + "/other")
    deliver(rig, b'{"a": 1}', topic="someone/else")
    assert adapter.poll_commands() is None


def test_the_command_queue_is_bounded_and_keeps_the_earliest_commands(rig, capsys):
    adapter = connected(rig)
    topic = (DEVICE + "/command").encode()
    for n in range(local_mqtt.MAX_PENDING_COMMANDS + 7):
        adapter._on_message(topic, json.dumps({"n": n}).encode())
    assert [c["n"] for c in adapter._pending] == list(range(local_mqtt.MAX_PENDING_COMMANDS))
    assert capsys.readouterr().out.count("queue full") == 7


def test_a_bad_message_never_breaks_polling_for_the_next_one(rig):
    adapter = connected(rig)
    deliver(rig, b"garbage")
    deliver(rig, b'{"ok": true}')
    assert adapter.poll_commands() == {"ok": True}
    assert adapter.poll_commands() is None


def test_the_io_timeout_is_rearmed_after_every_check(rig):
    adapter = connected(rig)
    adapter.poll_commands()
    assert rig.mqtt.client.sock.timeout == local_mqtt.IO_TIMEOUT_S  # check_msg() had reset it


def test_a_dead_socket_surfaces_at_the_ping(rig):
    adapter = connected(rig)
    rig.mqtt.ping_error = OSError(-1)
    with pytest.raises(OSError):
        adapter.poll_commands()


def test_polling_while_disconnected_is_a_connection_error(rig):
    with pytest.raises(OSError, match="not connected"):
        rig.make().poll_commands()


def test_announce_does_nothing_for_plain_mqtt(rig):
    assert connected(rig).announce([]) is None


# --- logging --------------------------------------------------------------------------

def test_logs_say_which_security_mode_is_in_use_and_never_show_credentials(rig, capsys):
    connected(rig, username="alice", password="Sup3rSecret", tls_verify=False)
    out = capsys.readouterr().out
    assert "TLS, broker NOT verified" in out
    assert "alice" not in out and "Sup3rSecret" not in out


def test_a_rejected_message_log_does_not_echo_the_payload(rig, capsys):
    adapter = connected(rig)
    deliver(rig, b"password=hunter2-please-do-not-log")
    adapter.poll_commands()
    assert "hunter2" not in capsys.readouterr().out


# --- raw publishing (used by Home Assistant discovery) ---------------------------

def test_publish_raw_sends_bytes_on_the_given_topic_and_can_retain(rig):
    adapter = connected(rig)
    adapter.publish_raw("homeassistant/sensor/x/config", b"{}", retain=True)
    assert rig.mqtt.client.published[-1] == (b"homeassistant/sensor/x/config", b"{}", True, 0)


def test_publish_raw_is_not_retained_by_default(rig):
    adapter = connected(rig)
    adapter.publish_raw("a/b", b"x")
    assert rig.mqtt.client.published[-1][2] is False


@pytest.mark.parametrize("bad", ["", "a/+/b", "a/#", "#", "+"])
def test_publish_raw_refuses_empty_and_wildcard_topics(rig, bad):
    adapter = connected(rig)
    with pytest.raises(ValueError):
        adapter.publish_raw(bad, b"x")


def test_publish_raw_while_disconnected_is_a_connection_error(rig):
    with pytest.raises(OSError, match="not connected"):
        rig.make().publish_raw("a/b", b"x")
