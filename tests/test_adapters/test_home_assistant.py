import json

import pytest

from firmware.adapters import home_assistant
from firmware.adapters.home_assistant import HomeAssistantAdapter

DEVICE = "e6616412abcdef01"
CONFIG = {"host": "broker.local", "device_id": DEVICE, "use_tls": False}  # plaintext: no TLS/clock to fake


class StubSensor:
    def __init__(self, name, entries, topic=None):
        self.name = name
        self.topic = topic or name
        self.entries = entries

    def discovery_payload(self):
        return self.entries


def temp_entry(key="temperature_c", **extra):
    entry = {
        "component": "sensor", "key": key, "name": "lounge temperature",
        "device_class": "temperature", "unit_of_measurement": "C",
        "state_class": "measurement", "value_template": "{{ value_json.%s }}" % key,
    }
    entry.update(extra)
    return entry


@pytest.fixture
def ha(fake_mqtt, tmp_path, monkeypatch):
    state = tmp_path / "ha_announced.json"
    monkeypatch.setattr(home_assistant, "STATE_PATH", str(state))

    class Rig:
        mqtt = fake_mqtt
        state_file = state

    rig = Rig()
    adapter = HomeAssistantAdapter(CONFIG)
    adapter.connect()
    rig.adapter = adapter
    rig.discovery = lambda: [
        (t.decode(), json.loads(p) if p else None, retain)
        for t, p, retain, _ in fake_mqtt.client.published
        if t.startswith(b"homeassistant/")
    ]
    return rig


def lounge():
    return StubSensor("lounge", [temp_entry(), temp_entry("humidity_pct", name="lounge humidity",
                                                          device_class="humidity", unit_of_measurement="%")])


# --- message format -------------------------------------------------------------

def test_one_retained_message_per_entry_on_the_discovery_topic(ha):
    ha.adapter.announce([lounge()])
    messages = ha.discovery()
    assert [m[0] for m in messages] == [
        "homeassistant/sensor/%s/lounge_temperature_c/config" % DEVICE,
        "homeassistant/sensor/%s/lounge_humidity_pct/config" % DEVICE,
    ]
    assert all(retain for _, _, retain in messages)


def test_the_payload_is_completed_with_state_topic_unique_id_and_availability(ha):
    ha.adapter.announce([lounge()])
    payload = ha.discovery()[0][1]
    assert payload["state_topic"] == DEVICE + "/lounge"
    assert payload["unique_id"] == DEVICE + "_lounge_temperature_c"
    assert payload["availability_topic"] == DEVICE + "/status"
    assert (payload["payload_available"], payload["payload_not_available"]) == ("online", "offline")
    assert payload["value_template"] == "{{ value_json.temperature_c }}"


def test_component_and_key_are_dropped_and_the_sensor_fields_are_kept(ha):
    ha.adapter.announce([lounge()])
    payload = ha.discovery()[0][1]
    assert "component" not in payload and "key" not in payload
    assert payload["device_class"] == "temperature"
    assert payload["unit_of_measurement"] == "C"
    assert payload["state_class"] == "measurement"
    assert payload["name"] == "lounge temperature"


def test_every_entity_belongs_to_one_device_named_after_the_board(ha):
    ha.adapter.announce([lounge(), StubSensor("door", [{
        "component": "binary_sensor", "key": "state", "name": "door",
        "value_template": "{{ value_json.state }}", "payload_on": "ON", "payload_off": "OFF"}])])
    devices = [m[1]["device"] for m in ha.discovery()]
    assert all(d == devices[0] for d in devices)
    assert devices[0]["identifiers"] == [DEVICE]
    assert devices[0]["name"] == "Pico IoT Kit EF01"
    assert devices[0]["model"] == "Raspberry Pi Pico W"


def test_binary_sensors_use_their_own_component_and_keep_their_payloads(ha):
    ha.adapter.announce([StubSensor("door", [{
        "component": "binary_sensor", "key": "state", "name": "door",
        "value_template": "{{ value_json.state }}", "payload_on": "ON", "payload_off": "OFF"}])])
    topic, payload, _ = ha.discovery()[0]
    assert topic == "homeassistant/binary_sensor/%s/door_state/config" % DEVICE
    assert (payload["payload_on"], payload["payload_off"]) == ("ON", "OFF")


def test_the_state_topic_is_the_sensors_topic_not_its_name(ha):
    ha.adapter.announce([StubSensor("lounge", [temp_entry()], topic="lounge")])
    assert ha.discovery()[0][1]["state_topic"] == DEVICE + "/lounge"


def test_sensors_without_discovery_are_skipped(ha):
    ha.adapter.announce([StubSensor("a", None), StubSensor("b", []), lounge()])
    assert len(ha.discovery()) == 2


def test_no_sensors_publishes_nothing(ha):
    ha.adapter.announce([])
    assert ha.discovery() == []


def test_payloads_are_small_valid_json(ha):
    ha.adapter.announce([lounge()])
    for _, payload, _, _ in [p for p in ha.mqtt.client.published if p[0].startswith(b"homeassistant/")]:
        assert len(payload) < 1000
        json.loads(payload)


def test_discovery_needs_a_connection(fake_mqtt, tmp_path, monkeypatch):
    monkeypatch.setattr(home_assistant, "STATE_PATH", str(tmp_path / "s.json"))
    with pytest.raises(OSError, match="not connected"):
        HomeAssistantAdapter(CONFIG).announce([lounge()])


# --- refusing unsafe input -------------------------------------------------------

@pytest.mark.parametrize("name", ["a/b", "a#", "a+", ""])
def test_unsafe_sensor_names_are_refused(ha, name):
    with pytest.raises(ValueError):
        ha.adapter.announce([StubSensor(name, [temp_entry()], topic="ok")])


def test_unsafe_keys_and_unknown_components_are_refused(ha):
    with pytest.raises(ValueError):
        ha.adapter.announce([StubSensor("a", [temp_entry(key="x/y")])])
    with pytest.raises(ValueError, match="component"):
        ha.adapter.announce([StubSensor("a", [temp_entry(component="switch")])])
    with pytest.raises(ValueError, match="component"):
        ha.adapter.announce([StubSensor("a", [temp_entry(component="../../x")])])


# --- the real drivers' entries fit the contract ---------------------------------

def test_the_real_drivers_produce_entries_the_adapter_accepts(ha, fake_hardware):
    from firmware.sensors.aht10 import Aht10Sensor
    from firmware.sensors.binary_input import BinaryInputSensor
    from firmware.sensors.dht11 import Dht11Sensor

    fake_hardware["machine"].I2C.return_value.readfrom.return_value = b"\x08"
    sensors = [
        Dht11Sensor("lounge", pin=5),
        Aht10Sensor("desk", sda=0, scl=1, sleep=lambda s: None),
        BinaryInputSensor("door", pin=15),
    ]
    ha.adapter.announce(sensors)
    messages = ha.discovery()
    assert len(messages) == 5  # 2 + 2 + 1
    uniques = [m[1]["unique_id"] for m in messages]
    assert len(set(uniques)) == len(uniques)
    for topic, payload, _ in messages:
        assert payload["state_topic"].startswith(DEVICE + "/")
        assert topic.startswith("homeassistant/") and topic.endswith("/config")


# --- ghost entities --------------------------------------------------------------

def test_announced_topics_are_remembered(ha):
    ha.adapter.announce([lounge()])
    saved = json.loads(ha.state_file.read_text())
    assert sorted(saved) == sorted(m[0] for m in ha.discovery())


def test_a_removed_sensor_is_cleared_from_home_assistant(ha):
    door = StubSensor("door", [{"component": "binary_sensor", "key": "state", "name": "door",
                                "value_template": "{{ value_json.state }}"}])
    ha.adapter.announce([lounge(), door])
    ha.mqtt.client.published.clear()
    ha.adapter.announce([lounge()])  # the door sensor is gone
    cleared = [(t.decode(), p, r) for t, p, r, _ in ha.mqtt.client.published if p == b""]
    assert cleared == [("homeassistant/binary_sensor/%s/door_state/config" % DEVICE, b"", True)]
    assert "door_state" not in ha.state_file.read_text()


def test_an_unchanged_announcement_clears_nothing_and_does_not_rewrite_the_file(ha, monkeypatch):
    ha.adapter.announce([lounge()])
    ha.mqtt.client.published.clear()
    writes = []
    real_open = open

    def watching_open(path, mode="r", *a, **k):
        if path == home_assistant.STATE_PATH and "w" in mode:
            writes.append(path)
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", watching_open)
    ha.adapter.announce([lounge()])
    assert [p for _, p, _, _ in ha.mqtt.client.published if p == b""] == []
    assert writes == []  # no flash wear on every reconnect


def test_a_renamed_sensor_clears_the_old_entities_and_adds_the_new(ha):
    ha.adapter.announce([lounge()])
    ha.mqtt.client.published.clear()
    ha.adapter.announce([StubSensor("kitchen", [temp_entry()])])
    published = [(t.decode(), p) for t, p, _, _ in ha.mqtt.client.published]
    assert ("homeassistant/sensor/%s/lounge_temperature_c/config" % DEVICE, b"") in published
    assert any(t.endswith("kitchen_temperature_c/config") and p for t, p in published)


@pytest.mark.parametrize("content", ["", "not json", "{}", '"text"', "42", "null"])
def test_a_damaged_state_file_is_ignored(ha, content):
    ha.state_file.write_text(content)
    ha.adapter.announce([lounge()])
    assert len(ha.discovery()) == 2


def test_only_topics_that_belong_to_this_device_are_ever_cleared(ha):
    hostile = [
        "homeassistant/sensor/someone-else/thing/config",
        "homeassistant/sensor/%s/x/state" % DEVICE,          # not a config topic
        "other/prefix/%s/x/config" % DEVICE,                  # not our prefix
        "homeassistant/#",
        "homeassistant/+/%s/x/config" % DEVICE,
        "homeassistant/sensor/%s/x/config" % DEVICE.replace("e", "f"),
        "homeassistant/sensor/someone-else/%s/config" % DEVICE,      # our id in the wrong place
        "homeassistant/sensor/someone-else/%s/y/config" % DEVICE,
        "homeassistant/light/%s/x/config" % DEVICE,                  # not a component we announce
        "homeassistant/sensor/%s/x/y/config" % DEVICE,               # too many segments
        42, None, ["nested"],
    ]
    ha.state_file.write_text(json.dumps(hostile))
    ha.adapter.announce([lounge()])
    assert [p for _, p, _, _ in ha.mqtt.client.published if p == b""] == []


def test_a_failed_publish_leaves_the_state_for_the_next_attempt(ha):
    ha.adapter.announce([lounge()])
    before = ha.state_file.read_text()
    ha.mqtt.publish_error = OSError(104)
    with pytest.raises(OSError):
        ha.adapter.announce([StubSensor("kitchen", [temp_entry()])])
    assert ha.state_file.read_text() == before


def test_an_unwritable_state_file_does_not_break_announcing(ha, monkeypatch, capsys):
    monkeypatch.setattr(home_assistant, "STATE_PATH", "/nonexistent-dir/ha.json")
    ha.adapter.announce([lounge()])
    assert len(ha.discovery()) == 2
    assert "could not remember" in capsys.readouterr().out
