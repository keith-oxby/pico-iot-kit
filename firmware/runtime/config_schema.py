"""Defines and validates the full on-disk config.json shape.

Used both when saving a new config (setup flow) and when loading on boot
-- a corrupted or incompatible config on load must fall back to SETUP
mode rather than crashing. Checklist item 3 (docs/architecture.md,
section 11).

The per-type field tables below intentionally duplicate the sensor/backend
type names from the registries instead of importing them, so this module
stays free of adapter (and hence hardware) imports. Adding a sensor or
backend type means adding a row here too.
"""

REQUIRED_TOP_LEVEL_KEYS = ("wifi", "backend", "sensors")

# User GPIOs on Pico W / Pico 2 W. GPIO 23, 24, 25 (and 29) are wired to the
# CYW43 Wi-Fi chip, so using them would break networking.
MAX_GPIO = 28
WIFI_CHIP_GPIOS = (23, 24, 25)

# RP2040/RP2350 I2C pin pairs: bus id -> (valid SDA pins, valid SCL pins)
I2C_BUSES = {
    0: ((0, 4, 8, 12, 16, 20), (1, 5, 9, 13, 17, 21)),
    1: ((2, 6, 10, 14, 18, 26), (3, 7, 11, 15, 19, 27)),
}

# Per-type fields: name -> (kind, required). Kinds: "str", "int", "bool",
# "gpio", "port", "i2c_id", "i2c_addr".
#
# Error messages are shown to people filling in the setup form, so they are
# plain sentences (capitalised, ending in a full stop) using the labels and
# hints below rather than field names or list indices.
BACKEND_FIELDS = {
    "local_mqtt": {
        "host": ("str", True),
        "port": ("port", False),
        "username": ("str", False),
        "password": ("str", False),
        "use_tls": ("bool", False),
        "tls_verify": ("bool", False),
    },
    # azure_iot_hub is deliberately absent until its adapter exists, so a config
    # naming it falls back to SETUP instead of retrying forever.
}
BACKEND_FIELDS["home_assistant"] = BACKEND_FIELDS["local_mqtt"]

SENSOR_FIELDS = {
    "dht11": {"pin": ("gpio", True)},
    "binary_input": {"pin": ("gpio", True), "invert": ("bool", False)},
    "aht10": {
        "sda": ("gpio", True),
        "scl": ("gpio", True),
        "i2c_id": ("i2c_id", False),
        "address": ("i2c_addr", False),
    },
}

_NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


FIELD_LABELS = {
    "host": "broker host",
    "port": "port",
    "username": "username",
    "password": "password",
    "use_tls": "TLS setting",
    "tls_verify": "certificate-check setting",
    "connection_string": "device connection string",
    "pin": "pin",
    "sda": "SDA pin",
    "scl": "SCL pin",
    "i2c_id": "I2C bus",
    "address": "I2C address",
    "invert": "invert setting",
}

KIND_HINTS = {
    "str": "must not be empty",
    "bool": "must be true or false",
    "int": "must be a whole number",
    "gpio": "must be a usable GPIO pin (GP0 to GP22 or GP26 to GP28)",
    "port": "must be a number from 1 to 65535",
    "i2c_id": "must be 0 or 1",
    "i2c_addr": "must be between 8 and 119",
}


class ConfigValidationError(Exception):
    pass


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _label(field):
    return FIELD_LABELS.get(field, field)


def _check_field(where, field, kind, value):
    if kind == "str":
        ok = isinstance(value, str) and value != ""
    elif kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "int":
        ok = _is_int(value) and value >= 0
    elif kind == "gpio":
        ok = _is_int(value) and 0 <= value <= MAX_GPIO and value not in WIFI_CHIP_GPIOS
    elif kind == "i2c_id":
        ok = _is_int(value) and value in I2C_BUSES
    elif kind == "i2c_addr":
        ok = _is_int(value) and 0x08 <= value <= 0x77
    elif kind == "port":
        ok = _is_int(value) and 1 <= value <= 65535
    else:
        raise ValueError("unknown field kind: %r" % kind)
    if not ok:
        raise ConfigValidationError(
            "%s: the %s %s." % (where, _label(field), KIND_HINTS[kind])
        )


def _check_typed_section(where, section, table):
    """Validate a {"type": ..., <fields>} section against a per-type table."""
    if not isinstance(section, dict):
        raise ConfigValidationError("%s: the settings are invalid." % where)
    type_name = section.get("type")
    if not isinstance(type_name, str) or type_name == "":
        raise ConfigValidationError("%s: choose a type." % where)
    fields = table.get(type_name)
    if fields is None:
        raise ConfigValidationError("%s: the type %r is not supported." % (where, type_name))
    for field, (kind, required) in fields.items():
        if field not in section:
            if required:
                raise ConfigValidationError(
                    "%s: the %s is required." % (where, _label(field))
                )
            continue
        _check_field(where, field, kind, section[field])


def _check_i2c_pins(where, entry):
    """sda/scl must be a valid pair on one bus, matching i2c_id if given."""
    for bus, (sda_pins, scl_pins) in I2C_BUSES.items():
        if entry["sda"] in sda_pins and entry["scl"] in scl_pins:
            if entry.get("i2c_id", bus) != bus:
                raise ConfigValidationError(
                    "%s: the I2C bus does not match the SDA and SCL pins." % where
                )
            return
    raise ConfigValidationError(
        "%s: SDA and SCL are not a valid I2C pin pair (for example GP0 with GP1, "
        "or GP2 with GP3)." % where
    )


# Extra cross-field checks per sensor type.
SENSOR_CHECKS = {"aht10": _check_i2c_pins}


def _check_wifi(wifi):
    if not isinstance(wifi, dict):
        raise ConfigValidationError("Wi-Fi: the settings are missing.")
    ssid = wifi.get("ssid")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
        raise ConfigValidationError("Wi-Fi: enter the network name (1 to 32 characters).")
    password = wifi.get("password")
    if not isinstance(password, str):
        raise ConfigValidationError("Wi-Fi: the password is not valid.")
    # WPA2 passphrases are 8-63 characters; empty means an open network.
    if password != "" and not 8 <= len(password) <= 63:
        raise ConfigValidationError(
            "Wi-Fi: the password must be 8 to 63 characters, or empty for an open network."
        )


def _sensor_label(index, entry):
    name = entry.get("name") if isinstance(entry, dict) else None
    if isinstance(name, str) and name != "":
        return "Sensor %r" % name
    return "Sensor %d" % (index + 1)


def _check_sensors(sensors):
    if not isinstance(sensors, list):
        raise ConfigValidationError("Sensors: the settings must be a list.")
    seen = set()
    for i, entry in enumerate(sensors):
        where = _sensor_label(i, entry)
        _check_typed_section(where, entry, SENSOR_FIELDS)
        # Sensor fields become constructor keyword arguments, so unknown
        # ones are rejected here rather than crashing at boot.
        allowed = SENSOR_FIELDS[entry["type"]]
        for key in entry:
            if key not in ("type", "name") and key not in allowed:
                raise ConfigValidationError("%s: the setting %r is not supported." % (where, key))
        name = entry.get("name")
        if not isinstance(name, str) or name == "":
            raise ConfigValidationError("%s: a name is required." % where)
        # names become MQTT topic segments, so no '/', '+' or '#'
        for ch in name:
            if ch not in _NAME_CHARS:
                raise ConfigValidationError(
                    "%s: names can only use letters, numbers, - and _." % where
                )
        if name in seen:
            raise ConfigValidationError(
                "%s: another sensor already uses this name; each needs its own." % where
            )
        seen.add(name)
        check = SENSOR_CHECKS.get(entry["type"])
        if check is not None:
            check(where, entry)


def validate(config):
    """Validate a config dict against the expected schema.

    Raises ConfigValidationError if invalid -- and only that, whatever the
    shape of the input. Returns the config unchanged if valid. Unknown extra
    keys are ignored (so newer configs stay loadable) everywhere except in
    sensor entries, whose fields are passed to driver constructors.
    """
    if not isinstance(config, dict):
        raise ConfigValidationError("The configuration must be an object.")

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in config:
            raise ConfigValidationError("The configuration is missing the %s section." % key)

    _check_wifi(config["wifi"])
    _check_typed_section("Backend", config["backend"], BACKEND_FIELDS)
    _check_sensors(config["sensors"])

    if "poll_interval_s" in config:
        interval = config["poll_interval_s"]
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval < 1:
            raise ConfigValidationError(
                "The reading interval must be a number of seconds, 1 or more."
            )

    return config
