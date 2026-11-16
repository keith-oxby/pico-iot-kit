"""HTTP routing for the setup portal, as pure functions over bytes/strings.

ap_portal.py owns the sockets; everything here is testable on the host.
The portal exposes exactly two things: the setup page (GET /) and the form
submit (POST /). Every other GET is redirected to the page, which is what
makes the OS captive-portal probes (iOS /hotspot-detect.html, Android
/generate_204, Windows /connecttest.txt, ...) open the setup page. There is
deliberately no endpoint that resets the device or re-enters setup mode
(docs/security.md).
"""

from firmware.runtime.config_schema import ConfigValidationError

from . import form

PORTAL_URL = "http://192.168.4.1/"
ERROR_MARKER = "<!--ERROR-->"
CA_MARKER = "<!--CA-->"
MAX_HEAD_BYTES = 2048

_STATUS = {
    200: "OK",
    302: "Found",
    400: "Bad Request",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}

_CONFIRMATION = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<title>Saved</title></head><body><h1>Settings saved</h1>"
    "<p>The device is restarting and will join your Wi-Fi network. "
    "You can close this page and reconnect your phone to your usual network.</p>"
    "</body></html>"
)


def html_escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def response(status, body="", extra_headers=()):
    """Build a complete HTTP/1.1 response (bytes)."""
    payload = body.encode()
    lines = [
        "HTTP/1.1 %d %s" % (status, _STATUS[status]),
        "Content-Type: text/html; charset=utf-8",
        "Content-Length: %d" % len(payload),
        "Connection: close",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self'",
    ]
    lines.extend(extra_headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + payload


def error_response(status):
    return response(status, "<!doctype html><title>Error</title><p>%s</p>" % _STATUS[status])


def redirect_response():
    return response(302, "", ("Location: " + PORTAL_URL,))


# Fields re-filled when the form is shown again after an error. Password-type
# fields (wifi_password, mqtt_password) are deliberately absent: secrets are
# never echoed back into the page.
REPOPULATE_TEXT = (
    "wifi_ssid", "mqtt_host", "mqtt_port", "mqtt_username",
    "dht11_name", "aht10_name", "binary_name", "poll_interval_s",
)
REPOPULATE_SELECT = ("backend_type", "dht11_pin", "aht10_sda", "aht10_scl", "binary_pin")
REPOPULATE_CHECKBOX = ("binary_invert",)
# radio group -> value selected when nothing was submitted
REPOPULATE_RADIO = {"mqtt_security": "verify"}

_CA_INSTALLED = "<small>CA certificate: installed.</small>"
_CA_MISSING = (
    "<small>CA certificate: <strong>not installed</strong>. Verified mode will not "
    "connect until one is installed from a PC (see the README).</small>"
)

_KEPT_NOTE = "Your other entries are kept. Passwords are not, so please re-enter them."


def _set_value(page, name, value):
    marker = 'name="%s"' % name
    i = page.find(marker)
    if i < 0:
        return page
    start = page.rfind("<", 0, i)
    end = page.find(">", i)
    tag = page[start:end]
    attr = ' value="%s"' % html_escape(value)
    j = tag.find(' value="')
    if j >= 0:
        k = tag.find('"', j + len(' value="'))
        tag = tag[:j] + attr + tag[k + 1:]
    else:
        tag = tag.replace(marker, marker + attr, 1)
    return page[:start] + tag + page[end:]


def _mark_selected(page, name, value):
    i = page.find('name="%s"' % name)
    if i < 0:
        return page
    option = '<option value="%s"' % html_escape(value)
    j = page.find(option, i)
    if j < 0 or j > page.find("</select>", i):
        return page
    j += len(option)
    return page[:j] + " selected" + page[j:]


def _mark_checked(page, name):
    marker = 'name="%s"' % name
    i = page.find(marker)
    if i < 0:
        return page
    i += len(marker)
    return page[:i] + " checked" + page[i:]


def _mark_radio(page, name, value):
    marker = 'name="%s" value="%s"' % (name, html_escape(value))
    i = page.find(marker)
    if i < 0:
        return page
    i += len(marker)
    return page[:i] + " checked" + page[i:]


def render_page(page, error=None, values=None, ca_installed=False):
    """Fill in the static setup page.

    Args:
        error: message to show in a banner (HTML-escaped).
        values: the submitted form fields; non-secret ones are put back into
            the form so the user doesn't have to start again.
        ca_installed: whether /ca.pem holds a usable CA, shown under the
            security choice.
    """
    if values:
        for name in REPOPULATE_TEXT:
            if name in values:
                page = _set_value(page, name, values[name])
        for name in REPOPULATE_SELECT:
            if name in values:
                page = _mark_selected(page, name, values[name])
        for name in REPOPULATE_CHECKBOX:
            if name in values:
                page = _mark_checked(page, name)
    for name, default in REPOPULATE_RADIO.items():
        chosen = values.get(name, default) if values else default
        page = _mark_radio(page, name, chosen)
    page = page.replace(CA_MARKER, _CA_INSTALLED if ca_installed else _CA_MISSING)
    message = ""
    if error:
        message = "<p><strong>The form could not be saved.</strong></p><p>%s</p>" % html_escape(error)
        if values:
            message += "<p>%s</p>" % _KEPT_NOTE
        message = '<div class="error">%s</div>' % message
    return page.replace(ERROR_MARKER, message)


def parse_request_head(head):
    """Parse the request line and headers (bytes, without the blank line).

    Returns (method, path, headers) with lowercased header names and the
    query string stripped from the path, or None if malformed.
    """
    if len(head) > MAX_HEAD_BYTES:
        return None
    try:
        lines = head.decode().split("\r\n")
    except ValueError:
        return None
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        return None
    method, target = parts[0], parts[1]
    if not target.startswith("/"):
        return None
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep:
            return None
        headers[name.strip().lower()] = value.strip()
    return method, target.split("?", 1)[0], headers


def content_length(headers):
    """Declared body length; raises ValueError if absent-invalid or too large."""
    raw = headers.get("content-length", "0")
    if raw == "" or not all(ch in "0123456789" for ch in raw):
        raise ValueError("bad content-length")
    length = int(raw)
    if length > form.MAX_BODY_BYTES:
        raise ValueError("body too large")
    return length


def handle_request(method, path, body, page, on_config, ca_installed=False):
    """Route one request.

    Args:
        page: the static setup page (str) containing ERROR_MARKER.
        on_config: called with the validated config; must persist it and may
            raise OSError, which is reported to the user as a save failure.
        ca_installed: whether a usable CA certificate is installed (shown on
            the page).

    Returns:
        (response_bytes, config) where config is the saved config once a
        submission succeeded, otherwise None.
    """
    if method == "GET":
        if path == "/":
            return response(200, render_page(page, ca_installed=ca_installed)), None
        return redirect_response(), None

    if method == "POST" and path == "/":
        fields = None
        try:
            fields = form.parse_form(body)
            config = form.build_config(fields)
        except ConfigValidationError as exc:
            return response(400, render_page(page, str(exc), fields, ca_installed)), None
        try:
            on_config(config)
        except Exception:
            message = "Could not save the settings. Please try again."
            return response(500, render_page(page, message, fields, ca_installed)), None
        return response(200, _CONFIRMATION), config

    return error_response(405), None
