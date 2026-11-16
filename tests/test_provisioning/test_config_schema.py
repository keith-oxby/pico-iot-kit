import copy
import re

import pytest

from firmware.runtime.config_schema import ConfigValidationError, validate


def good():
    return {
        "wifi": {"ssid": "home", "password": "hunter2hunter2"},
        "backend": {"type": "local_mqtt", "host": "192.168.1.10"},
        "sensors": [
            {"type": "dht11", "name": "lounge", "pin": 4},
            {"type": "aht10", "name": "desk", "sda": 0, "scl": 1},
            {"type": "binary_input", "name": "door", "pin": 15, "invert": True},
        ],
        "poll_interval_s": 30,
    }


def mutated(fn):
    config = good()
    fn(config)
    return config


def test_valid_config_is_returned_unchanged():
    config = good()
    assert validate(config) is config


def test_poll_interval_is_optional_and_extra_keys_are_ignored():
    config = good()
    del config["poll_interval_s"]
    config["future_option"] = 1
    assert validate(config) is config


def test_empty_sensor_list_is_valid():
    assert validate(mutated(lambda c: c.update(sensors=[])))


@pytest.mark.parametrize("backend", [
    {"type": "local_mqtt", "host": "h", "port": 8883, "username": "u",
     "password": "p", "use_tls": False},
    {"type": "home_assistant", "host": "ha.local"},
    {"type": "local_mqtt", "host": "h", "use_tls": True, "tls_verify": False},
    {"type": "local_mqtt", "host": "h", "use_tls": True, "tls_verify": True},
])
def test_valid_backends(backend):
    assert validate(mutated(lambda c: c.update(backend=backend)))


def test_open_wifi_network_allowed():
    assert validate(mutated(lambda c: c["wifi"].update(password="")))


BAD_CONFIGS = {
    "not a dict": [],
    "none": None,
    "missing wifi": lambda c: c.pop("wifi"),
    "missing backend": lambda c: c.pop("backend"),
    "missing sensors": lambda c: c.pop("sensors"),
    "wifi not dict": lambda c: c.update(wifi="x"),
    "wifi no ssid": lambda c: c["wifi"].pop("ssid"),
    "wifi empty ssid": lambda c: c["wifi"].update(ssid=""),
    "wifi long ssid": lambda c: c["wifi"].update(ssid="x" * 33),
    "wifi no password": lambda c: c["wifi"].pop("password"),
    "wifi short password": lambda c: c["wifi"].update(password="short"),
    "wifi long password": lambda c: c["wifi"].update(password="x" * 64),
    "wifi password not str": lambda c: c["wifi"].update(password=12345678),
    "backend not dict": lambda c: c.update(backend="local_mqtt"),
    "backend no type": lambda c: c["backend"].pop("type"),
    "backend type not str": lambda c: c["backend"].update(type=["local_mqtt"]),
    "backend unknown type": lambda c: c["backend"].update(type="aws"),
    "mqtt no host": lambda c: c["backend"].pop("host"),
    "mqtt empty host": lambda c: c["backend"].update(host=""),
    "mqtt port zero": lambda c: c["backend"].update(port=0),
    "mqtt port too big": lambda c: c["backend"].update(port=70000),
    "mqtt port str": lambda c: c["backend"].update(port="1883"),
    "mqtt use_tls not bool": lambda c: c["backend"].update(use_tls="yes"),
    "mqtt tls_verify not bool": lambda c: c["backend"].update(tls_verify="no"),
    "azure is not supported yet": lambda c: c.update(
        backend={"type": "azure_iot_hub", "connection_string": "HostName=x;DeviceId=y;SharedAccessKey=z"}),
    "sensors not list": lambda c: c.update(sensors={}),
    "sensor not dict": lambda c: c.update(sensors=["dht11"]),
    "sensor no type": lambda c: c["sensors"][0].pop("type"),
    "sensor unknown type": lambda c: c["sensors"][0].update(type="ds18b20"),
    "sensor no name": lambda c: c["sensors"][0].pop("name"),
    "sensor empty name": lambda c: c["sensors"][0].update(name=""),
    "sensor name with slash": lambda c: c["sensors"][0].update(name="a/b"),
    "sensor name with wildcard": lambda c: c["sensors"][0].update(name="a#"),
    "sensor duplicate name": lambda c: c["sensors"][1].update(name="lounge"),
    "dht11 no pin": lambda c: c["sensors"][0].pop("pin"),
    "dht11 pin out of range": lambda c: c["sensors"][0].update(pin=29),
    "dht11 negative pin": lambda c: c["sensors"][0].update(pin=-1),
    "dht11 pin bool": lambda c: c["sensors"][0].update(pin=True),
    "dht11 pin str": lambda c: c["sensors"][0].update(pin="4"),
    "aht10 no sda": lambda c: c["sensors"][1].pop("sda"),
    "aht10 no scl": lambda c: c["sensors"][1].pop("scl"),
    "aht10 bad i2c_id": lambda c: c["sensors"][1].update(i2c_id="0"),
    "aht10 i2c_id out of range": lambda c: c["sensors"][1].update(i2c_id=2),
    "aht10 i2c_id does not match pins": lambda c: c["sensors"][1].update(i2c_id=1),
    "aht10 bad address": lambda c: c["sensors"][1].update(address=-1),
    "aht10 reserved address": lambda c: c["sensors"][1].update(address=0x03),
    "aht10 address too high": lambda c: c["sensors"][1].update(address=0x78),
    "aht10 sda/scl swapped": lambda c: c["sensors"][1].update(sda=1, scl=0),
    "aht10 pins on different buses": lambda c: c["sensors"][1].update(sda=0, scl=3),
    "dht11 unknown field": lambda c: c["sensors"][0].update(invert=True),
    "binary unknown field": lambda c: c["sensors"][2].update(sda=0),
    "sensor typo field": lambda c: c["sensors"][0].update(pinn=4),
    "binary invert not bool": lambda c: c["sensors"][2].update(invert=1),
    "poll interval zero": lambda c: c.update(poll_interval_s=0),
    "poll interval str": lambda c: c.update(poll_interval_s="30"),
    "poll interval bool": lambda c: c.update(poll_interval_s=True),
}


@pytest.mark.parametrize("name", BAD_CONFIGS)
def test_invalid_configs_raise_only_validation_error(name):
    mutation = BAD_CONFIGS[name]
    config = mutation if not callable(mutation) else mutated(mutation)
    with pytest.raises(ConfigValidationError):
        validate(config)


def test_validate_does_not_modify_input():
    config = good()
    snapshot = copy.deepcopy(config)
    validate(config)
    assert config == snapshot


@pytest.mark.parametrize("pin", [23, 24, 25])
def test_wifi_chip_gpios_are_rejected(pin):
    config = good()
    config["sensors"][0]["pin"] = pin
    with pytest.raises(ConfigValidationError):
        validate(config)


@pytest.mark.parametrize("pin", [0, 22, 26, 28])
def test_user_gpios_next_to_the_wifi_pins_are_accepted(pin):
    config = good()
    config["sensors"][0]["pin"] = pin
    assert validate(config)


@pytest.mark.parametrize("sda,scl,extra", [
    (0, 1, {}),
    (0, 1, {"i2c_id": 0}),
    (2, 3, {"i2c_id": 1, "address": 0x39}),
    (26, 27, {}),
])
def test_valid_aht10_pin_pairs(sda, scl, extra):
    config = good()
    config["sensors"][1] = {"type": "aht10", "name": "desk", "sda": sda, "scl": scl, **extra}
    assert validate(config)


def message_for(mutation):
    config = mutation if not callable(mutation) else mutated(mutation)
    with pytest.raises(ConfigValidationError) as exc:
        validate(config)
    return str(exc.value)


@pytest.mark.parametrize("name", BAD_CONFIGS)
def test_error_messages_are_plain_sentences(name):
    message = message_for(BAD_CONFIGS[name])
    assert message[0].isupper(), message
    assert message.endswith("."), message
    assert not re.search(r"\w\[\d+\]", message), message  # no list indices
    assert "None" not in message, message


@pytest.mark.parametrize("mutation,expected", [
    (lambda c: c["backend"].pop("host"), "Backend: the broker host is required."),
    (lambda c: c["backend"].update(port=0), "Backend: the port must be a number from 1 to 65535."),
    (lambda c: c["wifi"].update(ssid=""), "Wi-Fi: enter the network name (1 to 32 characters)."),
    (lambda c: c["wifi"].update(password="short"),
     "Wi-Fi: the password must be 8 to 63 characters, or empty for an open network."),
    (lambda c: c["sensors"][0].update(pin=23),
     "Sensor 'lounge': the pin must be a usable GPIO pin (GP0 to GP22 or GP26 to GP28)."),
    (lambda c: c["sensors"][1].update(name="lounge"),
     "Sensor 'lounge': another sensor already uses this name; each needs its own."),
    (lambda c: c["sensors"][1].update(sda=0, scl=3),
     "Sensor 'desk': SDA and SCL are not a valid I2C pin pair (for example GP0 with GP1, "
     "or GP2 with GP3)."),
    (lambda c: c.update(poll_interval_s=0),
     "The reading interval must be a number of seconds, 1 or more."),
])
def test_common_errors_read_clearly(mutation, expected):
    assert message_for(mutation) == expected


def test_a_sensor_without_a_name_is_numbered():
    message = message_for(lambda c: c["sensors"][0].pop("name"))
    assert message == "Sensor 1: a name is required."


def test_azure_is_rejected_with_a_clear_message_until_it_exists():
    message = message_for(lambda c: c.update(backend={"type": "azure_iot_hub"}))
    assert message == "Backend: the type 'azure_iot_hub' is not supported."
