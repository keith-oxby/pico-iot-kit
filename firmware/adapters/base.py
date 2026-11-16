"""Abstract base class every downstream backend adapter implements.

A DownstreamAdapter owns the connection to wherever sensor data goes and
commands come from -- a local MQTT broker, Home Assistant, Azure IoT Hub,
etc. The Application Runtime (firmware/runtime/device.py) only knows this
interface, never the concrete backend.

See docs/adding-a-backend.md for the contributor guide.
"""


class DownstreamAdapter:
    """Base class for all downstream backend adapters."""

    def __init__(self, config):
        """
        Args:
            config: the "backend" section of config.json for this adapter
                type (e.g. broker host/port/credentials, or an IoT Hub
                connection string).
        """
        self.config = config

    def connect(self):
        """Establish the connection to the backend. Called once at startup."""
        raise NotImplementedError

    def publish(self, topic, reading):
        """Send one sensor reading.

        A reading is a dict with one key per measurement. Convention: send
        the whole dict as a single JSON message per sensor per poll, on
        `topic`; consumers (e.g. HA value templates) pick out individual keys.

        Args:
            topic: the sensor's `.topic` value (e.g. "temperature").
            reading: the JSON-serializable dict returned by SensorAdapter.read().
        """
        raise NotImplementedError

    def poll_commands(self):
        """Check for an incoming command.

        Returns:
            a command dict if one is pending, else None.
        """
        raise NotImplementedError

    def announce(self, sensors):
        """Optional: send discovery/registration metadata for the given
        sensors (e.g. Home Assistant MQTT discovery payloads). Adapters
        that don't support this can leave the default no-op.

        Args:
            sensors: list of SensorAdapter instances.
        """
        pass
