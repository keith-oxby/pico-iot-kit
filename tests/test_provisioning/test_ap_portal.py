import socket
import threading
import time

import pytest

from firmware.provisioning import ap_portal

PAGE = "<html><body><!--ERROR--><form>setup</form></body></html>"
GOOD_BODY = (
    b"wifi_ssid=home&wifi_password=hunter2hunter2&backend_type=local_mqtt"
    b"&mqtt_host=broker.local&dht11_pin=4"
)


# --- AP bring-up ------------------------------------------------------------

def test_ssid_uses_the_last_four_hex_digits_of_the_unique_id():
    assert ap_portal.ap_ssid_for_device(b"\xe6\x61\x64\x12\xab\xcd\xef\x01") == "PicoIoTKit-EF01"
    assert ap_portal.ap_ssid_for_device(bytes([1, 2, 3, 0xA1, 0xB2])) == "PicoIoTKit-A1B2"


def test_ssid_defaults_to_the_boards_unique_id(fake_hardware):
    fake_hardware["machine"].unique_id.return_value = b"\x00\x00\x00\x00\x0a\x0b"
    assert ap_portal.ap_ssid_for_device() == "PicoIoTKit-0A0B"


def test_start_ap_is_password_protected_and_uses_the_portal_address(fake_hardware):
    fake_hardware["machine"].unique_id.return_value = b"\x00\x00\x00\x00\xa1\xb2"
    ap = fake_hardware["network"].WLAN.return_value
    ap.active.return_value = True
    ap_portal.start_ap(sleep=lambda s: None)
    fake_hardware["network"].WLAN.assert_called_with(fake_hardware["network"].AP_IF)
    ap.config.assert_called_once_with(essid="PicoIoTKit-A1B2", password=ap_portal.DEFAULT_AP_PASSWORD)
    ap.active.assert_any_call(True)
    ap.ifconfig.assert_called_once_with(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))


def test_default_ap_password_meets_wpa2_minimum():
    assert len(ap_portal.DEFAULT_AP_PASSWORD) >= 8


@pytest.mark.parametrize("password", ["", None, "short", "1234567"])
def test_start_ap_refuses_an_open_or_weak_password(fake_hardware, password):
    with pytest.raises(ValueError):
        ap_portal.start_ap(ssid="x", password=password, sleep=lambda s: None)
    fake_hardware["network"].WLAN.assert_not_called()


def test_start_ap_times_out_if_the_radio_never_comes_up(fake_hardware):
    ap = fake_hardware["network"].WLAN.return_value
    ap.active.return_value = False
    sleeps = []
    with pytest.raises(OSError):
        ap_portal.start_ap(ssid="x", sleep=sleeps.append)
    assert len(sleeps) == ap_portal.AP_START_TIMEOUT_S


def test_load_page_requires_the_error_marker(tmp_path):
    good = tmp_path / "good.html"
    good.write_text(PAGE)
    assert ap_portal.load_page(str(good)) == PAGE
    bad = tmp_path / "bad.html"
    bad.write_text("<html></html>")
    with pytest.raises(ValueError):
        ap_portal.load_page(str(bad))


def test_the_real_setup_page_is_loadable_and_has_every_form_field():
    page = ap_portal.load_page("firmware/provisioning/templates/setup.html")
    for field in (
        "wifi_ssid", "wifi_password", "backend_type", "mqtt_host", "mqtt_port",
        "mqtt_username", "mqtt_password", "mqtt_security",
        "dht11_pin", "dht11_name", "aht10_sda", "aht10_scl", "aht10_name",
        "binary_pin", "binary_name", "binary_invert", "poll_interval_s",
    ):
        assert 'name="%s"' % field in page, field
    assert '<form method="post" action="/"' in page
    assert "<script" not in page
    # nothing weaker than the default may be pre-chosen
    assert " checked" not in page


# --- the serving loop, over real localhost sockets --------------------------

def free_port(kind):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Portal:
    """Runs serve_setup_portal in a thread on ephemeral localhost ports."""

    def __init__(self, on_config=None, page=PAGE):
        self.page = page
        self.http_port = free_port(socket.SOCK_STREAM)
        self.dns_port = free_port(socket.SOCK_DGRAM)
        self.saved = []
        self.result = None
        self.error = None
        self.on_config = on_config or self.saved.append
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._wait_until_listening()

    def _run(self):
        try:
            self.result = ap_portal.serve_setup_portal(
                self.on_config, page=self.page,
                http_addr=("127.0.0.1", self.http_port),
                dns_addr=("127.0.0.1", self.dns_port),
            )
        except Exception as exc:  # pragma: no cover - surfaced via .error
            self.error = exc

    def _wait_until_listening(self):
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.http_port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.02)
        raise AssertionError("portal never started listening")

    def request(self, raw, shutdown_write=False):
        with socket.create_connection(("127.0.0.1", self.http_port), timeout=3) as s:
            s.sendall(raw)
            if shutdown_write:
                s.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return data
                data += chunk

    def post(self, body):
        head = ("POST / HTTP/1.1\r\nHost: 192.168.4.1\r\nContent-Length: %d\r\n\r\n" % len(body)).encode()
        return self.request(head + body)

    def finish(self):
        self.thread.join(3)
        assert not self.thread.is_alive(), "portal did not return"
        assert self.error is None, self.error
        return self.result


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(ap_portal, "CLIENT_TIMEOUT_S", 0.3)


def test_serves_the_page_then_returns_the_config_after_confirming():
    portal = Portal()
    assert b"200 OK" in portal.request(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    response = portal.post(GOOD_BODY)
    assert b"200 OK" in response and b"Settings saved" in response
    config = portal.finish()
    assert config["backend"]["host"] == "broker.local"
    assert portal.saved == [config]


def test_probe_urls_are_redirected():
    portal = Portal()
    response = portal.request(b"GET /generate_204 HTTP/1.1\r\nHost: connectivitycheck.gstatic.com\r\n\r\n")
    assert b"302 Found" in response and b"Location: http://192.168.4.1/" in response
    portal.post(GOOD_BODY)
    portal.finish()


def test_invalid_submission_keeps_the_portal_running():
    portal = Portal()
    assert b"400 Bad Request" in portal.post(b"wifi_ssid=&backend_type=local_mqtt")
    assert portal.saved == []
    assert b"Settings saved" in portal.post(GOOD_BODY)
    portal.finish()


def test_save_failure_keeps_the_portal_running():
    attempts = []

    def flaky(config):
        attempts.append(config)
        if len(attempts) == 1:
            raise OSError("flash error")

    portal = Portal(on_config=flaky)
    assert b"500 Internal Server Error" in portal.post(GOOD_BODY)
    assert b"Settings saved" in portal.post(GOOD_BODY)
    portal.finish()
    assert len(attempts) == 2


def test_garbage_and_oversized_requests_do_not_stop_the_portal():
    portal = Portal()
    assert b"400" in portal.request(b"\x00\x01garbage\r\n\r\n")
    assert b"400" in portal.request(b"POST / HTTP/1.1\r\nContent-Length: 999999\r\n\r\n")
    try:
        # The 400 may be lost to a TCP reset (the server closes with unread
        # data); either way the portal must carry on.
        assert b"400" in portal.request(b"GET / HTTP/1.1\r\nX: " + b"a" * 5000)
    except ConnectionResetError:
        pass
    assert portal.request(b"GET / HTTP/1.1\r\n", shutdown_write=True) == b""  # client left early
    assert b"Settings saved" in portal.post(GOOD_BODY)
    portal.finish()


def test_a_stalled_client_only_delays_the_next_one():
    portal = Portal()
    stalled = socket.create_connection(("127.0.0.1", portal.http_port))  # sends nothing
    try:
        started = time.time()
        assert b"Settings saved" in portal.post(GOOD_BODY)
        assert time.time() - started < 3
    finally:
        stalled.close()
    portal.finish()


def test_dns_queries_get_the_portal_address():
    portal = Portal()
    query = (
        b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        b"\x07example\x03com\x00\x00\x01\x00\x01"
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(2)
        s.sendto(query, ("127.0.0.1", portal.dns_port))
        reply, _ = s.recvfrom(512)
    assert reply[:2] == b"\x12\x34"
    assert reply.endswith(bytes([192, 168, 4, 1]))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b"junk", ("127.0.0.1", portal.dns_port))  # ignored, no crash
    portal.post(GOOD_BODY)
    portal.finish()


REAL_PAGE_PATH = "firmware/provisioning/templates/setup.html"


def test_the_served_page_reflects_whether_a_ca_is_installed(tmp_path, monkeypatch):
    from firmware.runtime import ca_store

    ca = tmp_path / "ca.pem"
    monkeypatch.setattr(ca_store, "CA_PATH", str(ca))
    portal = Portal(page=ap_portal.load_page(REAL_PAGE_PATH))
    get = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
    assert "<strong>not installed</strong>" in portal.request(get).decode()

    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    page = portal.request(get).decode()
    assert "CA certificate: installed." in page
    assert "not installed" not in page
    portal.post(GOOD_BODY)
    portal.finish()


def test_an_idle_portal_keeps_checking_in_with_the_watchdog(monkeypatch):
    from firmware import watchdog

    checkins = []
    monkeypatch.setattr(ap_portal, "POLL_TIMEOUT_MS", 20)
    monkeypatch.setattr(watchdog, "checkin", lambda: checkins.append(1))
    portal = Portal()
    time.sleep(0.4)  # nobody is connecting: poll() must still wake up regularly
    assert len(checkins) >= 5
    portal.post(GOOD_BODY)
    portal.finish()
