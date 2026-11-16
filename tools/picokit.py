#!/usr/bin/env python3
"""picokit: PC-side helper for pico-iot-kit.

Verified TLS needs the board to trust your MQTT broker's certificate. This
tool makes the certificates and installs the CA on the board over USB, so
nothing has to be pasted on a phone and the device gains no network endpoint
for it.

    python tools/picokit.py make-certs --ip 192.168.0.10 --host mqtt.home.lan
    python tools/picokit.py install-ca pico-iot-kit-certs/ca.crt
    python tools/picokit.py status
    python tools/picokit.py remove-ca

Needs `pip install -r tools/requirements.txt` (cryptography and mpremote).
Every command that touches the board interrupts the firmware to talk to it,
so it ends with a soft reset (skip with --no-reset) to start it again.
"""

import argparse
import datetime
import hashlib
import ipaddress
import os
import re
import subprocess
import sys
import tempfile

# Must match firmware/runtime/ca_store.py (a test checks they agree).
MAX_CA_BYTES = 8192
CA_REMOTE_PATH = ":ca.pem"
DEFAULT_OUT_DIR = "pico-iot-kit-certs"
CA_DAYS = 3650
SERVER_DAYS = 1825
WARN_DAYS = 30

_PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

# What the board prints back, so the host can verify the copy.
_READBACK = (
    "import hashlib, binascii\n"
    "try:\n"
    "    d = open('/ca.pem', 'rb').read()\n"
    "    print('PICOKIT-SIZE', len(d))\n"
    "    print('PICOKIT-SHA256', binascii.hexlify(hashlib.sha256(d).digest()).decode())\n"
    "    print('PICOKIT-BEGIN')\n"
    "    print(d.decode())\n"
    "    print('PICOKIT-END')\n"
    "except OSError:\n"
    "    print('PICOKIT-NONE')\n"
)


class ToolError(Exception):
    """A problem to show the user as a plain message, not a traceback."""


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError:
        raise ToolError(
            "This needs the 'cryptography' package: pip install -r tools/requirements.txt"
        ) from None
    return x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID


# --------------------------------------------------------------------------
# Certificates
# --------------------------------------------------------------------------

def parse_names(hosts, ips):
    """Validate --host / --ip values; returns (dns_names, ip_addresses)."""
    names, addresses = [], []
    for host in hosts or ():
        if not _HOSTNAME.match(host):
            raise ToolError("'%s' is not a valid host name." % host)
        names.append(host)
    for value in ips or ():
        try:
            addresses.append(ipaddress.ip_address(value))
        except ValueError:
            raise ToolError("'%s' is not a valid IP address." % value) from None
    if not names and not addresses:
        raise ToolError(
            "Give the broker's address with at least one --ip or --host. The board checks "
            "that the certificate contains exactly the address it connects to."
        )
    return names, addresses


def _utc(cert_time):
    return cert_time if cert_time.tzinfo else cert_time.replace(tzinfo=datetime.timezone.utc)


def fingerprint(cert):
    _, hashes, *_ = _crypto()
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _write_new(path, data, private=False):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(path, flags, 0o600 if private else 0o644), "wb") as f:
        f.write(data)
    if private:
        os.chmod(path, 0o600)


def _key_pem(key):
    _, _, serialization, *_ = _crypto()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _cert_pem(cert):
    _, _, serialization, *_ = _crypto()
    return cert.public_bytes(serialization.Encoding.PEM)


def _new_ca(now, days):
    x509, hashes, _, ec, _, NameOID = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pico-iot-kit"),
        x509.NameAttribute(NameOID.COMMON_NAME, "pico-iot-kit CA %s" % now.strftime("%Y-%m-%d")),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _load_existing_ca(cert_path, key_path, now):
    x509, _, serialization, *_ = _crypto()
    try:
        cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
        key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise ToolError("Could not read the existing CA (%s). Fix or remove it." % exc) from None
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ToolError("The existing ca.crt and ca.key do not belong together.")
    if _utc(cert.not_valid_after_utc) < now + datetime.timedelta(days=WARN_DAYS):
        raise ToolError(
            "The existing CA expires within %d days; make a new one in a fresh directory "
            "and install it on the board." % WARN_DAYS)
    return key, cert


def make_certs(out_dir, hosts=(), ips=(), server_days=SERVER_DAYS, ca_days=CA_DAYS,
               force=False, now=None):
    """Create (or extend) a private CA and a broker certificate.

    If out_dir already holds ca.crt and ca.key, that CA is reused untouched,
    so a new broker certificate (new IP, renewal) needs no change on the
    board. Returns a dict with the file paths and the CA certificate.
    """
    x509, hashes, _, ec, ExtendedKeyUsageOID, NameOID = _crypto()
    names, addresses = parse_names(hosts, ips)
    now = now or datetime.datetime.now(datetime.timezone.utc)

    paths = {n: os.path.join(out_dir, n) for n in (
        "ca.crt", "ca.key", "broker.crt", "broker.key", "mosquitto-tls.conf")}
    have_ca = [os.path.exists(paths["ca.crt"]), os.path.exists(paths["ca.key"])]
    if any(have_ca) and not all(have_ca):
        raise ToolError("%s has only one of ca.crt and ca.key; refusing to guess." % out_dir)
    existing = [n for n in ("broker.crt", "broker.key", "mosquitto-tls.conf")
                if os.path.exists(paths[n])]
    if existing and not force:
        raise ToolError(
            "%s already exist in %s; use --force to replace them (the CA is kept)."
            % (", ".join(existing), out_dir))

    os.makedirs(out_dir, exist_ok=True)
    reused = all(have_ca)
    if reused:
        ca_key, ca_cert = _load_existing_ca(paths["ca.crt"], paths["ca.key"], now)
    else:
        ca_key, ca_cert = _new_ca(now, ca_days)

    key = ec.generate_private_key(ec.SECP256R1())
    common_name = (names + [str(a) for a in addresses])[0][:64]
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(a) for a in addresses]
    server_end = min(now + datetime.timedelta(days=server_days), _utc(ca_cert.not_valid_after_utc))
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(server_end)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    if not reused:
        _write_new(paths["ca.crt"], _cert_pem(ca_cert))
        _write_new(paths["ca.key"], _key_pem(ca_key), private=True)
    _write_new(paths["broker.crt"], _cert_pem(cert))
    _write_new(paths["broker.key"], _key_pem(key), private=True)
    _write_new(paths["mosquitto-tls.conf"], MOSQUITTO_CONF.encode())
    return {"paths": paths, "ca": ca_cert, "broker": cert, "ca_reused": reused,
            "names": names, "ips": addresses}


MOSQUITTO_CONF = """\
# Add this to your Mosquitto configuration, adjusting the paths to wherever you
# copy broker.crt and broker.key (Mosquitto must be able to read them).
# Keep ca.key and broker.key private; never copy them to the board.
listener 8883
certfile /mosquitto/config/broker.crt
keyfile /mosquitto/config/broker.key

# Also recommended: require a login instead of allowing anonymous clients.
#   allow_anonymous false
#   password_file /mosquitto/config/passwords
"""


# --------------------------------------------------------------------------
# Checking a CA before it goes on the board
# --------------------------------------------------------------------------

def _is_ca(cert):
    x509, *_ = _crypto()
    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return False


def load_ca_bundle(pem_text, now=None):
    """Check a CA file the way the board will, and return (certs, warnings).

    Raises ToolError for anything the board would refuse or that would never
    work: a private key, junk, an oversized file, expired certificates, or a
    server certificate that is signed by some other CA.
    """
    x509, *_ = _crypto()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if "PRIVATE KEY" in pem_text:
        raise ToolError(
            "That file contains a private key. Never put a private key on the board; "
            "install only the certificate (for example ca.crt).")
    if len(pem_text.encode()) > MAX_CA_BYTES:
        raise ToolError("That file is too large to be a CA certificate (limit %d bytes)." % MAX_CA_BYTES)
    blocks = _PEM_CERT.findall(pem_text)
    if not blocks:
        raise ToolError("That file does not contain a PEM certificate.")
    certs, warnings = [], []
    for block in blocks:
        try:
            cert = x509.load_pem_x509_certificate(block.encode())
        except ValueError:
            raise ToolError("One of the certificates in that file is damaged.") from None
        label = cert.subject.rfc4514_string()
        if _utc(cert.not_valid_after_utc) < now:
            raise ToolError("The certificate '%s' has expired." % label)
        if _utc(cert.not_valid_before_utc) > now + datetime.timedelta(days=1):
            raise ToolError("The certificate '%s' is not valid yet." % label)
        if not _is_ca(cert) and cert.subject != cert.issuer:
            raise ToolError(
                "'%s' is a server certificate signed by another CA, so the board cannot trust "
                "it directly. Install the CA that signed it." % label)
        if not _is_ca(cert):
            warnings.append(
                "'%s' is a self-signed server certificate, not a CA; it will only work for "
                "the broker that uses exactly this certificate." % label)
        if _utc(cert.not_valid_after_utc) < now + datetime.timedelta(days=WARN_DAYS):
            warnings.append("'%s' expires soon (%s)." % (label, _utc(cert.not_valid_after_utc).date()))
        certs.append(cert)
    return certs, warnings


def normalized_pem(certs):
    """Re-serialise the certificates: drops any text around the PEM blocks."""
    return b"".join(_cert_pem(c) for c in certs)


# --------------------------------------------------------------------------
# Talking to the board over USB (mpremote)
# --------------------------------------------------------------------------

def run_mpremote(port, steps):
    """Run mpremote steps (a list of argument lists) in one connection."""
    import importlib.util

    if importlib.util.find_spec("mpremote") is None:
        raise ToolError("This needs mpremote: pip install -r tools/requirements.txt")
    args = ["connect", port]
    for i, step in enumerate(steps):
        if i:
            args.append("+")
        args.extend(step)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mpremote"] + args,
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise ToolError("The board did not respond within 2 minutes.") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no details"]
        raise ToolError(
            "Could not talk to the board (%s). Is it plugged in over USB, and is nothing "
            "else (Thonny, screen, another mpremote) using the serial port?" % detail[0])
    return result


def _readback(output):
    """Parse the board's PICOKIT-* lines into a dict (or None if no CA)."""
    if "PICOKIT-NONE" in output:
        return None
    info = {}
    for line in output.splitlines():
        if line.startswith("PICOKIT-SIZE "):
            info["size"] = int(line.split()[1])
        elif line.startswith("PICOKIT-SHA256 "):
            info["sha256"] = line.split()[1]
    if "size" not in info or "sha256" not in info:
        raise ToolError("The board gave an unexpected answer; is the firmware or the REPL busy?")
    if "PICOKIT-BEGIN" in output and "PICOKIT-END" in output:
        info["pem"] = output.split("PICOKIT-BEGIN\n", 1)[1].split("PICOKIT-END", 1)[0]
    return info


def install_ca(path, port="auto", reset=True, runner=run_mpremote, out=print):
    try:
        text = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError("Could not read %s (%s)." % (path, exc)) from None
    certs, warnings = load_ca_bundle(text)
    for warning in warnings:
        out("warning: " + warning)
    data = normalized_pem(certs)
    local_sha = hashlib.sha256(data).hexdigest()

    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, "ca.pem")
        with open(staged, "wb") as f:
            f.write(data)
        steps = [["cp", staged, CA_REMOTE_PATH], ["exec", _READBACK]]
        if reset:
            steps.append(["soft-reset"])
        result = runner(port, steps)

    info = _readback(result.stdout)
    if info is None or info["sha256"] != local_sha or info["size"] != len(data):
        raise ToolError("The copy to the board did not verify; nothing was changed on purpose. Try again.")
    for cert in certs:
        out("installed: %s" % cert.subject.rfc4514_string())
        out("  SHA-256 fingerprint: %s" % fingerprint(cert))
    out("The board trusts this CA from its next connection%s." % (
        " (it was restarted)" if reset else "; restart it to apply now"))
    return certs


def ca_status(port="auto", reset=True, runner=run_mpremote, out=print):
    steps = [["exec", _READBACK]] + ([["soft-reset"]] if reset else [])
    info = _readback(runner(port, steps).stdout)
    if info is None:
        out("No CA certificate is installed on the board.")
        return None
    out("A CA file is installed (%d bytes, sha256 %s)." % (info["size"], info["sha256"][:16] + "..."))
    certs = []
    if "pem" in info:
        try:
            certs, warnings = load_ca_bundle(info["pem"])
        except ToolError as exc:
            out("But the board will not accept it: %s" % exc)
            return info
        for warning in warnings:
            out("warning: " + warning)
        for cert in certs:
            out("  %s" % cert.subject.rfc4514_string())
            out("  expires %s, SHA-256 %s" % (_utc(cert.not_valid_after_utc).date(), fingerprint(cert)))
    return info


def remove_ca(port="auto", reset=True, runner=run_mpremote, out=print):
    code = "import os\ntry:\n    os.remove('/ca.pem')\n    print('PICOKIT-REMOVED')\nexcept OSError:\n    print('PICOKIT-NONE')\n"
    steps = [["exec", code]] + ([["soft-reset"]] if reset else [])
    result = runner(port, steps)
    if "PICOKIT-REMOVED" in result.stdout:
        out("Removed the CA. Verified TLS will refuse to connect until you install another.")
        return True
    out("There was no CA installed.")
    return False


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def _cmd_make_certs(args):
    result = make_certs(args.out, args.host, args.ip, server_days=args.server_days, force=args.force)
    paths = result["paths"]
    print("%s the CA and made a broker certificate for: %s" % (
        "Reused" if result["ca_reused"] else "Created",
        ", ".join(result["names"] + [str(a) for a in result["ips"]])))
    for name in ("ca.crt", "ca.key", "broker.crt", "broker.key", "mosquitto-tls.conf"):
        print("  %s" % paths[name])
    print("CA SHA-256 fingerprint: %s" % fingerprint(result["ca"]))
    print("""
Next:
  1. Give Mosquitto broker.crt and broker.key (see mosquitto-tls.conf) and restart it.
  2. Install the CA on the board, connected over USB:
       python tools/picokit.py install-ca %s
  3. On the board's setup page choose "Verified TLS" and enter the broker as exactly
     one of the names above.
Keep ca.key and broker.key private. ca.key can issue certificates the board will trust;
keep it somewhere safe, since you need it to add another broker address later.""" % paths["ca.crt"])


def _cmd_install_ca(args):
    install_ca(args.file, args.port, reset=not args.no_reset)


def _cmd_status(args):
    ca_status(args.port, reset=not args.no_reset)


def _cmd_remove_ca(args):
    remove_ca(args.port, reset=not args.no_reset)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="picokit", description="PC-side helper for pico-iot-kit (certificates and CA install).")
    sub = parser.add_subparsers(dest="command", required=True)

    def board_options(p):
        p.add_argument("--port", default="auto", help="serial port, or 'auto' (default)")
        p.add_argument("--no-reset", action="store_true",
                       help="leave the board at the REPL instead of restarting its firmware")

    p = sub.add_parser("make-certs", help="create a CA and a broker certificate")
    p.add_argument("--ip", action="append", default=[], help="broker IP address (repeatable)")
    p.add_argument("--host", action="append", default=[], help="broker host name (repeatable)")
    p.add_argument("--out", default=DEFAULT_OUT_DIR, help="output directory (default %(default)s)")
    p.add_argument("--server-days", type=int, default=SERVER_DAYS, help="broker certificate lifetime")
    p.add_argument("--force", action="store_true", help="replace an existing broker certificate")
    p.set_defaults(func=_cmd_make_certs)

    p = sub.add_parser("install-ca", help="install a CA certificate on the board over USB")
    p.add_argument("file", help="the CA certificate (PEM), e.g. ca.crt")
    board_options(p)
    p.set_defaults(func=_cmd_install_ca)

    p = sub.add_parser("status", help="show which CA is installed on the board")
    board_options(p)
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("remove-ca", help="remove the CA from the board")
    board_options(p)
    p.set_defaults(func=_cmd_remove_ca)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ToolError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
