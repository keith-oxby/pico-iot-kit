"""Azure IoT Hub adapter.

Materially different from the MQTT-broker adapters: authenticates with a
device connection string, generates a time-limited SAS token, and talks
to IoT Hub's specific MQTT endpoint and topic structure.

Checklist item 7 (docs/architecture.md, section 11).
"""

from .base import DownstreamAdapter


class AzureIotHubAdapter(DownstreamAdapter):
    """Connects to Azure IoT Hub over MQTT using SAS token auth."""

    def __init__(self, config):
        super().__init__(config)
        # Expected config keys: connection_string (parsed into hostname,
        # device_id, shared_access_key).
        # TODO: parse the connection string.

    def connect(self):
        # TODO: generate a SAS token from the shared access key, connect
        # over MQTT (TLS only -- required by IoT Hub) using the token as
        # the password, subscribe to
        # "devices/{device_id}/messages/devicebound/#".
        raise NotImplementedError

    def publish(self, topic, reading):
        # TODO: publish JSON-encoded `reading` to
        # "devices/{device_id}/messages/events/" with `topic` as a
        # property on the message, per IoT Hub's conventions.
        raise NotImplementedError

    def poll_commands(self):
        # TODO: check the devicebound subscription for a message, decode
        # it, return it (or None). Re-generate the SAS token on expiry.
        raise NotImplementedError
