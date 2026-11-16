"""The CA certificate that verified TLS trusts, kept at /ca.pem.

It is installed from a PC over USB (tools/, Sprint 3 item 7d), not through
the setup page, and lives outside config.json: it is public data, and a
BOOTSEL config reset should not remove it. Only certificates are accepted;
a file containing a private key is rejected so a mistaken install of the
wrong file can never leave a key on the device.
"""

CA_PATH = "/ca.pem"
MAX_CA_BYTES = 8192

_BEGIN = "-----BEGIN CERTIFICATE-----"
_END = "-----END CERTIFICATE-----"


class CaError(Exception):
    pass


def load():
    """Return the installed CA as PEM text, or raise CaError saying why not."""
    pem = None
    try:
        with open(CA_PATH) as f:
            pem = f.read(MAX_CA_BYTES + 1)
    except (OSError, ValueError):
        pass
    if pem is None:
        raise CaError("No CA certificate is installed on the device.")
    if len(pem) > MAX_CA_BYTES:
        raise CaError("The installed CA file is too large to be a certificate.")
    if "PRIVATE KEY" in pem:
        raise CaError(
            "The installed CA file contains a private key. "
            "Remove it and install only the CA certificate."
        )
    if _BEGIN not in pem or _END not in pem:
        raise CaError("The installed CA file is not a PEM certificate.")
    return pem


def installed():
    """True if a usable CA certificate is installed."""
    try:
        load()
    except CaError:
        return False
    return True
