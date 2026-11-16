import pytest

from firmware.provisioning import config_store, form
from firmware.runtime.config_schema import ConfigValidationError


def encode(fields):
    from urllib.parse import urlencode

    return urlencode(fields).encode()


BASE = {
    "wifi_ssid": "home",
    "wifi_password": "hunter2hunter2",
    "backend_type": "local_mqtt",
    "mqtt_host": "broker.local",
}


def build(**overrides):
    fields = dict(BASE, **overrides)
    return form.build_config(form.parse_form(encode(fields)))


# --- parse_form -----------------------------------------------------------

def test_parse_form_decodes_plus_percent_and_utf8():
    body = b"a=b+c&p=%26%3D%25&u=%C3%A9&empty=&novalue"
    assert form.parse_form(body) == {"a": "b c", "p": "&=%", "u": "é", "empty": "", "novalue": ""}


def test_parse_form_empty_body():
    assert form.parse_form(b"") == {}


def test_last_duplicate_wins():
    assert form.parse_form(b"a=1&a=2") == {"a": "2"}


@pytest.mark.parametrize("body", [
    b"a=%",
    b"a=%4",
    b"a=%zz",
    b"a=%+1",  # int("+1", 16) would succeed; must not
    b"a=%ff",  # lone 0xff byte is not valid UTF-8
    b"\xff\xfe=1",
])
def test_parse_form_rejects_malformed_input(body):
    with pytest.raises(ConfigValidationError):
        form.parse_form(body)


def test_parse_form_limits():
    with pytest.raises(ConfigValidationError):
        form.parse_form(b"a=" + b"x" * form.MAX_BODY_BYTES)
    with pytest.raises(ConfigValidationError):
        form.parse_form("&".join("k%d=1" % i for i in range(form.MAX_FIELDS + 1)).encode())


# --- build_config ---------------------------------------------------------

def test_minimal_mqtt_config():
    config = build()
    assert config == {
        "wifi": {"ssid": "home", "password": "hunter2hunter2"},
        "backend": {"type": "local_mqtt", "host": "broker.local", "use_tls": True, "tls_verify": True},
        "sensors": [],
    }


@pytest.mark.parametrize("choice,use_tls,tls_verify", [
    ("verify", True, True),
    ("unverified", True, False),
    ("plaintext", False, True),  # tls_verify stays true so use_tls can't later mean "unverified"
])
def test_each_security_choice_maps_to_explicit_settings(choice, use_tls, tls_verify):
    backend = build(mqtt_security=choice)["backend"]
    assert (backend["use_tls"], backend["tls_verify"]) == (use_tls, tls_verify)


def test_the_safe_choice_is_the_default_when_nothing_is_submitted():
    backend = build()["backend"]
    assert (backend["use_tls"], backend["tls_verify"]) == (True, True)


@pytest.mark.parametrize("value", ["", "none", "VERIFY", "plaintext ", "0"])
def test_an_unknown_security_choice_is_an_error_not_a_downgrade(value):
    with pytest.raises(ConfigValidationError, match="Choose how to secure"):
        build(mqtt_security=value)


def test_the_old_plaintext_checkbox_no_longer_downgrades_anything():
    assert build(mqtt_plaintext="on")["backend"]["use_tls"] is True


def test_mqtt_options_and_home_assistant():
    config = build(backend_type="home_assistant", mqtt_port="8883",
                   mqtt_username="u", mqtt_password="p w")
    assert config["backend"] == {
        "type": "home_assistant", "host": "broker.local", "port": 8883,
        "username": "u", "password": "p w", "use_tls": True, "tls_verify": True,
    }


def test_azure_is_no_longer_accepted_by_the_form():
    with pytest.raises(ConfigValidationError, match="not supported"):
        build(backend_type="azure_iot_hub", azure_connection_string="HostName=h;DeviceId=d")


def test_all_three_sensors_and_interval():
    config = build(
        dht11_pin="4", dht11_name="lounge",
        aht10_sda="0", aht10_scl="1",
        binary_pin="15", binary_invert="on",
        poll_interval_s="45",
    )
    assert config["sensors"] == [
        {"type": "dht11", "name": "lounge", "pin": 4},
        {"type": "aht10", "name": "aht10", "sda": 0, "scl": 1},
        {"type": "binary_input", "name": "binary_input", "pin": 15, "invert": True},
    ]
    assert config["poll_interval_s"] == 45


def test_unticked_invert_is_recorded_as_false():
    config = build(binary_pin="15")
    assert config["sensors"][0]["invert"] is False


def test_blank_pin_means_not_connected():
    assert build(dht11_pin="", aht10_sda="", aht10_scl="", binary_pin="")["sensors"] == []


def test_password_is_not_stripped_but_host_is():
    config = build(wifi_password="  spaced pass  ", mqtt_host="  broker.local ")
    assert config["wifi"]["password"] == "  spaced pass  "
    assert config["backend"]["host"] == "broker.local"


@pytest.mark.parametrize("overrides", [
    {"wifi_ssid": ""},
    {"wifi_password": "short"},
    {"backend_type": ""},
    {"backend_type": "aws"},
    {"mqtt_host": ""},
    {"mqtt_port": "abc"},
    {"mqtt_port": "-1"},
    {"mqtt_port": "99999"},
    {"dht11_pin": "x"},
    {"dht11_pin": "23"},  # wifi chip pin
    {"dht11_pin": "4", "binary_pin": "4", "dht11_name": "same", "binary_name": "same"},
    {"aht10_sda": "0"},  # scl missing
    {"aht10_sda": "0", "aht10_scl": "3"},  # different buses
    {"poll_interval_s": "0"},
    {"poll_interval_s": "1.5"},
    {"backend_type": "azure_iot_hub"},  # no connection string
])
def test_invalid_submissions_raise_validation_error(overrides):
    with pytest.raises(ConfigValidationError):
        build(**overrides)


@pytest.mark.parametrize("overrides", [
    {"mqtt_port": "bad"},
    {"dht11_pin": "bad"},
    {"dht11_pin": "23"},
    {"aht10_sda": "0"},
    {"poll_interval_s": "bad"},
    {"wifi_ssid": ""},
    {"mqtt_host": ""},
])
def test_error_messages_never_contain_passwords(overrides):
    secret = "TopSecret-9f3a"
    with pytest.raises(ConfigValidationError) as exc:
        build(wifi_password=secret, mqtt_password=secret, azure_connection_string=secret, **overrides)
    assert secret not in str(exc.value)


def test_built_config_is_accepted_by_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "TMP_PATH", str(tmp_path / "config.json.tmp"))
    config = build(dht11_pin="4", aht10_sda="2", aht10_scl="3", binary_pin="15")
    config_store.save(config)
    assert config_store.load() == config


@pytest.mark.parametrize("body", [
    b"a=%",
    b"a=%zz",
    b"a=%ff",
    b"a=" + b"x" * form.MAX_BODY_BYTES,
    "&".join("k%d=1" % i for i in range(form.MAX_FIELDS + 1)).encode(),
])
def test_unreadable_form_messages_are_plain_sentences(body):
    with pytest.raises(ConfigValidationError) as exc:
        form.parse_form(body)
    message = str(exc.value)
    assert message[0].isupper() and message.endswith("."), message


@pytest.mark.parametrize("overrides,expected", [
    ({"mqtt_port": "abc"}, "MQTT port must be a whole number."),
    ({"dht11_pin": "x"}, "DHT11 pin must be a whole number."),
    ({"poll_interval_s": "1.5"}, "Reading interval must be a whole number."),
    ({"aht10_sda": "0"}, "AHT10 needs both an SDA pin and an SCL pin."),
    ({"mqtt_host": ""}, "Backend: the broker host is required."),
    ({"wifi_ssid": ""}, "Wi-Fi: enter the network name (1 to 32 characters)."),
])
def test_form_errors_read_clearly(overrides, expected):
    with pytest.raises(ConfigValidationError) as exc:
        build(**overrides)
    assert str(exc.value) == expected
