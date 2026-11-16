"""Home Assistant adapter -- composes LocalMqttAdapter rather than
duplicating transport logic, and adds HA's MQTT discovery protocol so
sensors auto-register as HA entities.

Checklist item 7 (docs/architecture.md, section 11).

For every entry of every sensor's `discovery_payload()` it publishes one
retained message to
    homeassistant/<component>/<device_id>/<sensor>_<key>/config
completed with what only the adapter knows: the state topic (the sensor's
whole reading dict is one JSON message, which each entry's value_template
picks a key out of), a unique id, the device block, and the availability
topic (the retained online/offline status from LocalMqttAdapter).

Changing sensors usually means a BOOTSEL reset and fresh setup, and retained
discovery messages outlive the config that created them, so Home Assistant
would keep "ghost" entities. The topics announced are remembered in
/ha_announced.json and any that disappear are cleared with an empty retained
message. Only topics under this device's own discovery prefix are ever
cleared, whatever the file says.
"""

import json

from .local_mqtt import LocalMqttAdapter, _check_segment

DISCOVERY_PREFIX = "homeassistant"
STATE_PATH = "/ha_announced.json"
COMPONENTS = ("sensor", "binary_sensor")
MODEL = "Raspberry Pi Pico W"


class HomeAssistantAdapter(LocalMqttAdapter):
    """Same MQTT transport as LocalMqttAdapter, plus HA discovery."""

    def announce(self, sensors):
        current = []
        for sensor in sensors:
            for entry in sensor.discovery_payload() or ():
                topic, payload = self._discovery_message(sensor, entry)
                self.publish_raw(topic, payload, retain=True)
                current.append(topic)
        self._clear_stale(current)

    def _discovery_message(self, sensor, entry):
        component = entry["component"]
        if component not in COMPONENTS:
            raise ValueError("unsupported Home Assistant component: %r" % component)
        _check_segment(sensor.name, "sensor name")
        _check_segment(sensor.topic, "sensor topic")
        _check_segment(entry["key"], "reading key")
        object_id = "%s_%s" % (sensor.name, entry["key"])

        config = {k: v for k, v in entry.items() if k not in ("component", "key")}
        config["state_topic"] = "%s/%s" % (self.device_id, sensor.topic)
        config["unique_id"] = "%s_%s" % (self.device_id, object_id)
        config["device"] = {
            "identifiers": [self.device_id],
            "name": "Pico IoT Kit %s" % self.device_id[-4:].upper(),
            "manufacturer": "pico-iot-kit",
            "model": MODEL,
        }
        config["availability_topic"] = self.status_topic
        config["payload_available"] = "online"
        config["payload_not_available"] = "offline"

        topic = "%s/%s/%s/%s/config" % (DISCOVERY_PREFIX, component, self.device_id, object_id)
        return topic, json.dumps(config).encode()

    def _is_ours(self, topic):
        """True only for exactly homeassistant/<component>/<device_id>/<object>/config."""
        if not isinstance(topic, str) or "+" in topic or "#" in topic:
            return False
        parts = topic.split("/")
        return (
            len(parts) == 5
            and parts[0] == DISCOVERY_PREFIX
            and parts[1] in COMPONENTS
            and parts[2] == self.device_id
            and parts[3] != ""
            and parts[4] == "config"
        )

    def _load_announced(self):
        try:
            with open(STATE_PATH) as f:
                topics = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(topics, list):
            return []
        return [t for t in topics if self._is_ours(t)]

    def _clear_stale(self, current):
        previous = self._load_announced()
        for topic in previous:
            if topic not in current:
                self.publish_raw(topic, b"", retain=True)
        if sorted(previous) != sorted(current):
            try:
                with open(STATE_PATH, "w") as f:
                    json.dump(current, f)
            except OSError:
                print("ha: could not remember the announced entities")
