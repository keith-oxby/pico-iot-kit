"""Connection security for the MQTT backends: three explicit modes.

- verified:   TLS, and the broker's certificate must chain to the CA in
              /ca.pem (firmware/runtime/ca_store.py) and match the address
              the device connects to. Needs the clock set (runtime/clock.py).
- unverified: TLS without checking who the broker is. Stops passive
              sniffing but not an active impersonator. Explicit opt-in.
- plaintext:  no encryption at all. Explicit opt-in.

Verified is the default. A verified connection never falls back to a weaker
mode: if the CA is missing or unusable, building the context fails with a
message saying so (docs/security.md).

On a Pico W (checked on hardware): the hostname check cannot be skipped
(CERT_REQUIRED needs server_hostname), an IP address works if it is in the
certificate's Subject Alternative Names, and the certificate dates are
checked against the RTC.
"""

from firmware.runtime import ca_store

VERIFIED = "verified"
UNVERIFIED = "unverified"
PLAINTEXT = "plaintext"

LABELS = {
    VERIFIED: "TLS, broker verified",
    UNVERIFIED: "TLS, broker NOT verified",
    PLAINTEXT: "plaintext, NOT encrypted",
}


class TlsConfigError(Exception):
    pass


def mode_for(backend_config):
    """Map the backend's use_tls / tls_verify settings to a mode."""
    if not backend_config.get("use_tls", True):
        return PLAINTEXT
    if backend_config.get("tls_verify", True):
        return VERIFIED
    return UNVERIFIED


def needs_clock(mode):
    return mode == VERIFIED


def make_context(mode):
    """Return an ssl.SSLContext for `mode`, or None for plaintext.

    Raises TlsConfigError if verified mode can't be set up.
    """
    if mode == PLAINTEXT:
        return None
    if mode not in (VERIFIED, UNVERIFIED):
        raise ValueError("unknown security mode: %r" % mode)

    pem = None
    problem = None
    if mode == VERIFIED:
        try:
            pem = ca_store.load()
        except ca_store.CaError as exc:
            problem = str(exc)
    if problem is not None:
        raise TlsConfigError(
            problem + " Install one from a PC (see the README), or choose another security mode."
        )

    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if mode == UNVERIFIED:
        context.verify_mode = ssl.CERT_NONE
    else:
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=pem)
    return context
