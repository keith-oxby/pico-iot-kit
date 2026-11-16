"""Turns the setup form's POST body into a validated config dict.

Pure functions, no I/O, so the whole form -> config path is testable on the
host. Field names here must match firmware/provisioning/templates/setup.html.
All problems surface as ConfigValidationError with a message that is safe
to show the user: it never contains a password or connection string. It may
contain a backend/sensor type or sensor name the user typed, so callers must
HTML-escape it (portal_http does).
"""

from firmware.runtime.config_schema import ConfigValidationError, validate

MAX_BODY_BYTES = 4096
MAX_FIELDS = 40

_HEX = "0123456789abcdefABCDEF"
_DIGITS = "0123456789"
_MQTT_TYPES = ("local_mqtt", "home_assistant")

# mqtt_security radio -> (use_tls, tls_verify). Plaintext keeps tls_verify true
# so that flipping use_tls on later can never silently mean "unverified".
SECURITY_MODES = {
    "verify": (True, True),
    "unverified": (True, False),
    "plaintext": (False, True),
}
DEFAULT_SECURITY = "verify"


def _decode(data):
    """bytes -> str, or raise ConfigValidationError (never inside an except)."""
    text = None
    try:
        text = data.decode()
    except ValueError:
        pass
    if text is None:
        raise ConfigValidationError("The form data could not be read.")
    return text


def _unquote(text):
    text = text.replace("+", " ")
    if "%" not in text:
        return text
    parts = text.split("%")
    out = bytearray(parts[0].encode())
    for part in parts[1:]:
        if len(part) < 2 or part[0] not in _HEX or part[1] not in _HEX:
            raise ConfigValidationError("The form data could not be read.")
        out.append(int(part[:2], 16))
        out += part[2:].encode()
    return _decode(out)


def parse_form(body):
    """Decode an application/x-www-form-urlencoded body (bytes) to a dict."""
    if len(body) > MAX_BODY_BYTES:
        raise ConfigValidationError("The form data is too large.")
    text = _decode(body)
    fields = {}
    if text:
        pairs = text.split("&")
        if len(pairs) > MAX_FIELDS:
            raise ConfigValidationError("The form has too many fields.")
        for pair in pairs:
            key, _, value = pair.partition("=")
            fields[_unquote(key)] = _unquote(value)
    return fields


def _int(fields, key, label):
    raw = fields.get(key, "").strip()
    if raw == "":
        return None
    for ch in raw:
        if ch not in _DIGITS:
            raise ConfigValidationError("%s must be a whole number." % label)
    return int(raw)


def _put(target, key, value):
    if value is not None and value != "":
        target[key] = value


def _build_backend(fields):
    backend_type = fields.get("backend_type", "")
    backend = {"type": backend_type}
    if backend_type in _MQTT_TYPES:
        _put(backend, "host", fields.get("mqtt_host", "").strip())
        _put(backend, "port", _int(fields, "mqtt_port", "MQTT port"))
        _put(backend, "username", fields.get("mqtt_username", ""))
        _put(backend, "password", fields.get("mqtt_password", ""))
        # Weaker modes are explicit opt-ins; always record the choice.
        security = fields.get("mqtt_security", DEFAULT_SECURITY)
        if security not in SECURITY_MODES:
            raise ConfigValidationError("Choose how to secure the MQTT connection.")
        backend["use_tls"], backend["tls_verify"] = SECURITY_MODES[security]
    return backend


def _build_sensors(fields):
    sensors = []

    pin = _int(fields, "dht11_pin", "DHT11 pin")
    if pin is not None:
        sensors.append({"type": "dht11", "name": fields.get("dht11_name") or "dht11", "pin": pin})

    sda = _int(fields, "aht10_sda", "AHT10 SDA pin")
    scl = _int(fields, "aht10_scl", "AHT10 SCL pin")
    if sda is not None or scl is not None:
        if sda is None or scl is None:
            raise ConfigValidationError("AHT10 needs both an SDA pin and an SCL pin.")
        sensors.append(
            {"type": "aht10", "name": fields.get("aht10_name") or "aht10", "sda": sda, "scl": scl}
        )

    pin = _int(fields, "binary_pin", "Binary input pin")
    if pin is not None:
        sensors.append(
            {
                "type": "binary_input",
                "name": fields.get("binary_name") or "binary_input",
                "pin": pin,
                "invert": "binary_invert" in fields,
            }
        )
    return sensors


def build_config(fields):
    """Build and validate a config dict from parsed form fields."""
    config = {
        "wifi": {
            "ssid": fields.get("wifi_ssid", ""),
            "password": fields.get("wifi_password", ""),
        },
        "backend": _build_backend(fields),
        "sensors": _build_sensors(fields),
    }
    interval = _int(fields, "poll_interval_s", "Reading interval")
    if interval is not None:
        config["poll_interval_s"] = interval
    return validate(config)
