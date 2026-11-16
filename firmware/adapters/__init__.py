"""Downstream adapter registry.

Maps the backend `type` string used in config.json to its
DownstreamAdapter subclass. Adding a new backend (docs/adding-a-backend.md)
means adding one new file next to this one and registering it here -- no
other file needs to change.
"""

from .base import DownstreamAdapter
from .home_assistant import HomeAssistantAdapter
from .local_mqtt import LocalMqttAdapter

BACKEND_TYPES = {
    "local_mqtt": LocalMqttAdapter,
    "home_assistant": HomeAssistantAdapter,
}
# azure_iot_hub.py stays in the tree as an unfinished stub; it is not registered
# (and the config schema rejects it) until the Azure sprint.


def load_downstream_adapter(backend_config):
    """Build a DownstreamAdapter instance from the "backend" section of
    config.json.

    Args:
        backend_config: dict like {"type": "local_mqtt", "host": "...", ...}

    Returns:
        a DownstreamAdapter instance.
    """
    cls = BACKEND_TYPES.get(backend_config["type"])
    if cls is None:
        raise ValueError("Unknown backend type: %r" % backend_config["type"])
    return cls(backend_config)
