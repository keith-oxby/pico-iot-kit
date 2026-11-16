"""Setup-mode access point + captive-portal web server.

Starts the CYW43 chip in AP mode with a password-protected SSID, serves
the setup form, answers every DNS query with the device's own address so
phones detect the captive portal, and validates + hands off submitted
config. Checklist item 4 (docs/architecture.md, section 11).

This module is the socket/network glue only; request handling lives in
portal_http.py, form parsing in form.py and DNS in captive_dns.py, all of
which are pure and tested on the host.

Security (docs/architecture.md, section 8): the AP is never open --
DEFAULT_AP_PASSWORD must always be set. The portal is single-threaded and
handles one client at a time with a short socket timeout, so a stalled
client can delay but not hang setup. Nothing submitted is logged.
"""

import time

from firmware import watchdog
from firmware.runtime import ca_store

from . import captive_dns, portal_http

DEFAULT_AP_PASSWORD = "setupme123"  # Documented in the README; a future
                                    # revision may randomize it per device.
AP_IP = "192.168.4.1"
AP_NETMASK = "255.255.255.0"
SETUP_PAGE_PATH = "/firmware/provisioning/templates/setup.html"
CLIENT_TIMEOUT_S = 5
POLL_TIMEOUT_MS = 1000  # wake up to check in with the watchdog while idle
AP_START_TIMEOUT_S = 10


def ap_ssid_for_device(unique_id=None):
    """Build the per-device SSID, e.g. PicoIoTKit-A1B2 (last 4 hex of the ID)."""
    if unique_id is None:
        import machine

        unique_id = machine.unique_id()
    return "PicoIoTKit-%02X%02X" % (unique_id[-2], unique_id[-1])


def start_ap(ssid=None, password=DEFAULT_AP_PASSWORD, sleep=time.sleep):
    """Bring up the Wi-Fi chip in AP mode and return once it is active.

    Refuses an empty or too-short password: WPA2 needs at least 8
    characters, and the setup AP must never be open.
    """
    if not password or len(password) < 8:
        raise ValueError("setup AP must be password-protected (8+ characters)")
    import network

    ap = network.WLAN(network.AP_IF)
    ap.config(essid=ssid or ap_ssid_for_device(), password=password)
    ap.active(True)
    waited = 0
    while not ap.active():
        if waited >= AP_START_TIMEOUT_S:
            raise OSError("setup AP did not start")
        sleep(1)
        waited += 1
    ap.ifconfig((AP_IP, AP_NETMASK, AP_IP, AP_IP))
    return ap


def _frozen_page():
    """The page generated into the frozen image by build/stage.py, or None
    on a dev board (and on the host), where the HTML file is read instead."""
    page = None
    try:
        from . import _setup_page

        page = _setup_page.PAGE
    except ImportError:
        pass
    return page


def load_page(path=None):
    """The setup page. With no path: the frozen copy if there is one,
    otherwise SETUP_PAGE_PATH. An explicit path always reads that file."""
    page = _frozen_page() if path is None else None
    if page is None:
        with open(SETUP_PAGE_PATH if path is None else path) as f:
            page = f.read()
    if portal_http.ERROR_MARKER not in page:
        raise ValueError("setup page is missing the error marker")
    return page


def _bind(sock, host, port):
    sock.bind(_getaddr(host, port))


def _getaddr(host, port):
    import socket

    return socket.getaddrinfo(host, port)[0][-1]


def _send_all(conn, data):
    while data:
        sent = conn.send(data)
        data = data[sent:]


def _read_request(conn):
    """Read one request. Returns ((method, path, body), None) on success, or
    (None, error_response_or_None) if it was malformed or the client left.
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return None, None
        buf += chunk
        if b"\r\n\r\n" not in buf and len(buf) > portal_http.MAX_HEAD_BYTES:
            return None, portal_http.error_response(400)
    head, _, body = buf.partition(b"\r\n\r\n")
    request = portal_http.parse_request_head(head)
    if request is None:
        return None, portal_http.error_response(400)
    method, path, headers = request
    length = None
    try:
        length = portal_http.content_length(headers)
    except ValueError:
        pass
    if length is None:
        return None, portal_http.error_response(400)
    while len(body) < length:
        chunk = conn.recv(length - len(body))
        if not chunk:
            return None, None
        body += chunk
    return (method, path, body[:length]), None


def _serve_one(listener, page, on_config):
    """Handle one connection; returns the saved config on a successful submit."""
    conn, _ = listener.accept()
    try:
        conn.settimeout(CLIENT_TIMEOUT_S)
        request, error = _read_request(conn)
        if request is None:
            if error:
                _send_all(conn, error)
            return None
        method, path, body = request
        raw, config = portal_http.handle_request(
            method, path, body, page, on_config, ca_store.installed()
        )
        _send_all(conn, raw)
        return config
    finally:
        conn.close()


def _answer_dns(sock, ip):
    data, addr = sock.recvfrom(512)
    reply = captive_dns.build_response(data, ip)
    if reply:
        sock.sendto(reply, addr)


def serve_setup_portal(
    on_config_submitted,
    page=None,
    http_addr=("0.0.0.0", 80),
    dns_addr=("0.0.0.0", 53),
    dns_ip=AP_IP,
):
    """Serve the setup page and DNS until a valid config is submitted.

    Args:
        on_config_submitted: called with the validated config to persist it
            (e.g. config_store.save). If it raises, the user is told the save
            failed and the portal keeps running.
        page: the setup page HTML; loaded from SETUP_PAGE_PATH if omitted.

    Returns:
        the saved config, after the confirmation page has been sent. The
        caller is responsible for restarting the device.
    """
    import select
    import socket

    if page is None:
        page = load_page()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # DNS first, so a client that can reach the page can already resolve.
        _bind(dns, *dns_addr)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _bind(listener, *http_addr)
        listener.listen(2)

        # poll() reports socket objects on MicroPython but file descriptors
        # on CPython (where the tests run), so key the lookup accordingly.
        sockets = {}
        poller = select.poll()
        for sock in (listener, dns):
            sockets[sock.fileno() if hasattr(sock, "fileno") else sock] = sock
            poller.register(sock, select.POLLIN)
        while True:
            watchdog.checkin()
            for item in poller.poll(POLL_TIMEOUT_MS):
                try:
                    if sockets[item[0]] is dns:
                        _answer_dns(dns, dns_ip)
                        continue
                    config = _serve_one(listener, page, on_config_submitted)
                except Exception as exc:
                    # A bad client must never take the portal down.
                    # Type and errno only: never anything a client submitted.
                    print("portal: request failed:", type(exc).__name__, exc.args if isinstance(exc, OSError) else "")
                    continue
                if config is not None:
                    return config
    finally:
        listener.close()
        dns.close()

