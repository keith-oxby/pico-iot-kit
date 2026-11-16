import pytest

from firmware.adapters import BACKEND_TYPES, load_downstream_adapter
from firmware.runtime import config_schema


def test_registry_and_schema_agree_on_backend_types():
    assert set(BACKEND_TYPES) == set(config_schema.BACKEND_FIELDS)


def test_azure_is_unregistered_and_rejected_until_its_sprint():
    assert "azure_iot_hub" not in BACKEND_TYPES
    assert "azure_iot_hub" not in config_schema.BACKEND_FIELDS
    with pytest.raises(ValueError):
        load_downstream_adapter({"type": "azure_iot_hub"})


def test_the_mqtt_backends_share_one_set_of_fields():
    assert config_schema.BACKEND_FIELDS["home_assistant"] is config_schema.BACKEND_FIELDS["local_mqtt"]


def test_the_registry_builds_the_mqtt_adapters_from_a_runtime_style_config():
    from firmware.adapters.home_assistant import HomeAssistantAdapter
    from firmware.adapters.local_mqtt import LocalMqttAdapter

    config = {"host": "broker.local", "device_id": "e6616412abcdef01"}
    assert type(load_downstream_adapter(dict(config, type="local_mqtt"))) is LocalMqttAdapter
    assert type(load_downstream_adapter(dict(config, type="home_assistant"))) is HomeAssistantAdapter
