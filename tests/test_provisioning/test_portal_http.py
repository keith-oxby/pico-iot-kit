import re

import pytest

from firmware.provisioning import ap_portal
from firmware.provisioning import portal_http as http

PAGE = "<html><body><!--ERROR--><form>setup</form></body></html>"
GOOD_BODY = (
    b"wifi_ssid=home&wifi_password=hunter2hunter2&backend_type=local_mqtt"
    b"&mqtt_host=broker.local&dht11_pin=4"
)


def status_of(raw):
    return int(raw.split(b" ", 2)[1])


def body_of(raw):
    return raw.split(b"\r\n\r\n", 1)[1].decode()


def headers_of(raw):
    head = raw.split(b"\r\n\r\n", 1)[0].decode().split("\r\n")[1:]
    return {k.lower(): v for k, _, v in (line.partition(": ") for line in head)}


class Saver:
    def __init__(self, error=None):
        self.saved = []
        self.error = error

    def __call__(self, config):
        if self.error:
            raise self.error
        self.saved.append(config)


# --- routing --------------------------------------------------------------

def test_get_root_serves_the_page():
    raw, config = http.handle_request("GET", "/", b"", PAGE, Saver())
    assert status_of(raw) == 200
    assert "<form>setup</form>" in body_of(raw)
    assert "<!--ERROR-->" not in body_of(raw)
    assert config is None


@pytest.mark.parametrize("path", [
    "/hotspot-detect.html",       # iOS / macOS
    "/library/test/success.html",  # older iOS
    "/generate_204",              # Android
    "/gen_204",
    "/connecttest.txt",           # Windows
    "/ncsi.txt",
    "/redirect",
    "/anything/else",
])
def test_other_gets_redirect_to_the_portal(path):
    raw, config = http.handle_request("GET", path, b"", PAGE, Saver())
    assert status_of(raw) == 302
    assert headers_of(raw)["location"] == "http://192.168.4.1/"
    assert config is None


@pytest.mark.parametrize("method", ["PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
def test_other_methods_are_rejected(method):
    raw, _ = http.handle_request(method, "/", b"", PAGE, Saver())
    assert status_of(raw) == 405


def test_post_to_other_paths_is_rejected():
    raw, _ = http.handle_request("POST", "/reset", b"", PAGE, Saver())
    assert status_of(raw) == 405


def test_there_is_no_network_path_to_reset_or_clear_config():
    saver = Saver()
    for path in ("/reset", "/clear", "/setup", "/factory-reset", "/reboot"):
        for method in ("GET", "POST"):
            raw, config = http.handle_request(method, path, GOOD_BODY, PAGE, saver)
            assert config is None
    assert saver.saved == []


# --- submit ---------------------------------------------------------------

def test_valid_post_saves_and_confirms():
    saver = Saver()
    raw, config = http.handle_request("POST", "/", GOOD_BODY, PAGE, saver)
    assert status_of(raw) == 200
    assert "Settings saved" in body_of(raw)
    assert saver.saved == [config]
    assert config["backend"]["use_tls"] is True
    assert config["sensors"] == [{"type": "dht11", "name": "dht11", "pin": 4}]


def test_invalid_post_shows_the_form_again_with_an_error_and_saves_nothing():
    saver = Saver()
    raw, config = http.handle_request("POST", "/", b"wifi_ssid=&backend_type=local_mqtt", PAGE, saver)
    assert status_of(raw) == 400
    assert 'class="error"' in body_of(raw)
    assert "<form>setup</form>" in body_of(raw)
    assert config is None and saver.saved == []


def test_malformed_post_is_a_400_not_a_crash():
    raw, config = http.handle_request("POST", "/", b"a=%zz", PAGE, Saver())
    assert status_of(raw) == 400 and config is None


def test_save_failure_is_reported_and_not_confirmed():
    raw, config = http.handle_request("POST", "/", GOOD_BODY, PAGE, Saver(OSError("flash full")))
    assert status_of(raw) == 500
    assert "Settings saved" not in body_of(raw)
    assert "flash full" not in body_of(raw)
    assert config is None


def test_reflected_text_is_html_escaped():
    body = b"wifi_ssid=home&wifi_password=hunter2hunter2&backend_type=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    raw, _ = http.handle_request("POST", "/", body, PAGE, Saver())
    assert b"<script>" not in raw
    assert "&lt;script&gt;" in body_of(raw)


def test_submitted_secrets_are_never_echoed():
    body = GOOD_BODY.replace(b"hunter2hunter2", b"TopSecretPass1") + b"&mqtt_port=bad"
    raw, _ = http.handle_request("POST", "/", body, PAGE, Saver())
    assert b"TopSecretPass1" not in raw


def test_responses_carry_security_headers_and_correct_length():
    raw, _ = http.handle_request("GET", "/", b"", PAGE, Saver())
    headers = headers_of(raw)
    assert headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    assert int(headers["content-length"]) == len(raw.split(b"\r\n\r\n", 1)[1])


def test_non_ascii_body_length_is_in_bytes():
    raw = http.response(200, "café")
    assert int(headers_of(raw)["content-length"]) == 5


# --- request parsing ------------------------------------------------------

def test_parse_request_head():
    head = b"POST /?x=1 HTTP/1.1\r\nHost: 192.168.4.1\r\nContent-Length: 12\r\nX-A:  b "
    assert http.parse_request_head(head) == (
        "POST", "/", {"host": "192.168.4.1", "content-length": "12", "x-a": "b"}
    )


@pytest.mark.parametrize("head", [
    b"",
    b"GET /",
    b"GET / FTP/1.0",
    b"GET http://evil/ HTTP/1.1",
    b"GET relative HTTP/1.1",
    b"GET / HTTP/1.1\r\nno-colon-header",
    b"GET / HTTP/1.1\r\n\xff: x",
    b"GET / HTTP/1.1\r\n" + b"X: " + b"a" * http.MAX_HEAD_BYTES,
])
def test_malformed_heads_are_rejected(head):
    assert http.parse_request_head(head) is None


@pytest.mark.parametrize("headers,expected", [
    ({}, 0),
    ({"content-length": "0"}, 0),
    ({"content-length": "123"}, 123),
])
def test_content_length(headers, expected):
    assert http.content_length(headers) == expected


@pytest.mark.parametrize("value", ["", "-1", "abc", "1.5", "99999", "0x10"])
def test_bad_or_oversized_content_length_raises(value):
    with pytest.raises(ValueError):
        http.content_length({"content-length": value})


# --- form values survive a validation error ---------------------------------

REAL_PAGE = ap_portal.load_page("firmware/provisioning/templates/setup.html")


def submit_invalid(extra=b"", base=None):
    """POST the real page a form that is missing the MQTT host."""
    body = base or (
        b"wifi_ssid=MyNet&wifi_password=TopSecretWifi1&backend_type=home_assistant"
        b"&mqtt_host=&mqtt_username=bob&mqtt_password=TopSecretMqtt2"
        b"&mqtt_security=unverified&dht11_pin=4&dht11_name=lounge"
        b"&aht10_sda=2&aht10_scl=3&binary_pin=15&binary_invert=on&poll_interval_s=45"
    ) + extra
    raw, config = http.handle_request("POST", "/", body, REAL_PAGE, Saver())
    assert status_of(raw) == 400 and config is None
    return body_of(raw)


def tag_for(page, name):
    return re.search(r"<(?:input|select)[^>]*name=\"%s\"[^>]*>" % name, page).group(0)


def selected_value(page, name):
    start = page.index('name="%s"' % name)
    end = page.index("</select>", start)
    return re.findall(r'<option value="([^"]*)" selected', page[start:end])


def test_the_real_page_keeps_what_the_user_typed():
    page = submit_invalid()
    assert 'value="MyNet"' in tag_for(page, "wifi_ssid")
    assert 'value="bob"' in tag_for(page, "mqtt_username")
    assert 'value="lounge"' in tag_for(page, "dht11_name")
    assert 'value="45"' in tag_for(page, "poll_interval_s")
    assert selected_value(page, "backend_type") == ["home_assistant"]
    assert selected_value(page, "dht11_pin") == ["4"]
    assert selected_value(page, "aht10_sda") == ["2"]
    assert selected_value(page, "aht10_scl") == ["3"]
    assert selected_value(page, "binary_pin") == ["15"]
    assert "checked" in tag_for(page, "binary_invert")
    assert 'class="error"' in page


def test_an_existing_default_value_is_replaced_not_duplicated():
    tag = tag_for(submit_invalid(), "dht11_name")
    assert tag.count("value=") == 1


def test_passwords_and_the_connection_string_are_never_echoed():
    page = submit_invalid()
    for secret in ("TopSecretWifi1", "TopSecretMqtt2"):
        assert secret not in page
    for name in ("wifi_password", "mqtt_password"):
        assert "value=" not in tag_for(page, name)
    assert "re-enter" in page


def checked_radios(page, name="mqtt_security"):
    return re.findall(r'name="%s" value="([^"]*)" checked' % name, page)


def test_the_chosen_security_option_is_kept_after_an_error():
    assert checked_radios(submit_invalid()) == ["unverified"]
    plaintext = b"wifi_ssid=x&backend_type=local_mqtt&mqtt_security=plaintext"
    assert checked_radios(submit_invalid(base=plaintext)) == ["plaintext"]


def test_the_safe_option_is_selected_when_none_was_submitted():
    assert checked_radios(submit_invalid(base=b"wifi_ssid=x&backend_type=local_mqtt")) == ["verify"]


def test_a_forged_security_value_selects_nothing_extra():
    body = b"wifi_ssid=x&backend_type=local_mqtt&mqtt_security=%22+checked+x%3D%22"
    assert checked_radios(submit_invalid(base=body)) == []


def test_repopulated_values_are_html_escaped():
    body = (b"wifi_ssid=%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E&backend_type=local_mqtt"
            b"&mqtt_host=&dht11_name=%22+onfocus%3D%22x")
    page = submit_invalid(base=body)
    assert "<script>" not in page
    assert "&lt;script&gt;" in tag_for(page, "wifi_ssid")
    assert 'onfocus="x' not in page


def test_unknown_select_values_do_not_select_anything():
    page = submit_invalid(b"&binary_pin=99999")
    assert selected_value(page, "binary_pin") == []


def test_a_fresh_get_has_only_the_safe_defaults_chosen():
    raw, _ = http.handle_request("GET", "/", b"", REAL_PAGE, Saver())
    page = body_of(raw)
    assert " selected" not in page
    assert checked_radios(page) == ["verify"]
    assert page.count(" checked") == 1
    assert 'name="dht11_name" value="dht11"' in page
    assert 'name="poll_interval_s" value="30"' in page


def test_the_page_shows_whether_a_ca_is_installed():
    def fetch(installed):
        raw, _ = http.handle_request("GET", "/", b"", REAL_PAGE, Saver(), ca_installed=installed)
        return body_of(raw)

    assert "CA certificate: installed." in fetch(True)
    assert "not installed" not in fetch(True)
    assert "<strong>not installed</strong>" in fetch(False)
    assert "<!--CA-->" not in fetch(True) and "<!--CA-->" not in fetch(False)


def test_the_ca_status_is_shown_again_after_an_error():
    body = b"wifi_ssid=&backend_type=local_mqtt"
    raw, _ = http.handle_request("POST", "/", body, REAL_PAGE, Saver(), ca_installed=True)
    assert "CA certificate: installed." in body_of(raw)


def test_every_security_option_is_explained_and_the_weak_ones_are_labelled():
    assert "Verified TLS (recommended)" in REAL_PAGE
    assert "Encrypted, not verified (less secure)" in REAL_PAGE
    assert "Plaintext (not encrypted)" in REAL_PAGE
    assert "Azure" not in REAL_PAGE


def test_nothing_is_pre_ticked_in_the_raw_page_file():
    assert " checked" not in REAL_PAGE
    assert " selected" not in REAL_PAGE


def test_malformed_posts_show_the_blank_form_with_the_error():
    raw, _ = http.handle_request("POST", "/", b"a=%zz", REAL_PAGE, Saver())
    page = body_of(raw)
    assert status_of(raw) == 400 and 'class="error"' in page
    assert "re-enter" not in page  # nothing was kept, so nothing to say


def test_every_form_field_is_either_repopulated_or_a_password():
    names = re.findall(r'<(?:input|select)[^>]*name="([^"]+)"', REAL_PAGE)
    password_fields = {
        n for n in names if 'type="password"' in tag_for(REAL_PAGE, n)
    }
    assert password_fields == {"wifi_password", "mqtt_password"}
    repopulated = set(
        http.REPOPULATE_TEXT + http.REPOPULATE_SELECT + http.REPOPULATE_CHECKBOX
        + tuple(http.REPOPULATE_RADIO)
    )
    assert set(names) - password_fields == repopulated
    assert not repopulated & password_fields


def test_the_error_banner_separates_the_problem_from_the_password_note():
    page = submit_invalid()
    banner = re.search(r'<div class="error">(.*?)</div>', page).group(1)
    paragraphs = re.findall(r"<p>(.*?)</p>", banner)
    assert paragraphs == [
        "<strong>The form could not be saved.</strong>",
        "Backend: the broker host is required.",
        "Your other entries are kept. Passwords are not, so please re-enter them.",
    ]


def test_the_banner_has_no_password_note_when_nothing_was_kept():
    raw, _ = http.handle_request("POST", "/", b"a=%zz", REAL_PAGE, Saver())
    banner = re.search(r'<div class="error">(.*?)</div>', body_of(raw)).group(1)
    assert len(re.findall(r"<p>", banner)) == 2
