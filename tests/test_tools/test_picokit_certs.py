import datetime
import os
import shutil
import socket
import ssl
import stat
import subprocess
import threading

import pytest

pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from tools import picokit  # noqa: E402

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def made(tmp_path):
    return picokit.make_certs(str(tmp_path / "certs"), hosts=["broker.home.lan"],
                              ips=["192.168.0.10", "127.0.0.1"], now=NOW)


def san(cert):
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return ext.get_values_for_type(x509.DNSName), [str(i) for i in ext.get_values_for_type(x509.IPAddress)]


# --- what is generated -----------------------------------------------------------

def test_the_broker_certificate_names_exactly_what_was_asked(made):
    names, ips = san(made["broker"])
    assert names == ["broker.home.lan"]
    assert ips == ["192.168.0.10", "127.0.0.1"]


def test_the_broker_certificate_is_a_server_certificate_not_a_ca(made):
    cert = made["broker"]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
    assert cert.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature


def test_the_ca_is_a_constrained_ca_that_can_only_sign(made):
    bc = made["ca"].extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True and bc.path_length == 0
    usage = made["ca"].extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and not usage.digital_signature


def test_the_broker_certificate_is_signed_by_the_ca(made):
    ca = made["ca"]
    ca.public_key().verify(made["broker"].signature, made["broker"].tbs_certificate_bytes,
                           ec.ECDSA(hashes.SHA256()))
    assert made["broker"].issuer == ca.subject


def test_validity_windows(made):
    broker, ca = made["broker"], made["ca"]
    assert broker.not_valid_before_utc == NOW - datetime.timedelta(days=1)  # tolerates clock skew
    assert broker.not_valid_after_utc == NOW + datetime.timedelta(days=picokit.SERVER_DAYS)
    assert ca.not_valid_after_utc == NOW + datetime.timedelta(days=picokit.CA_DAYS)
    assert broker.not_valid_after_utc <= ca.not_valid_after_utc


def test_the_server_certificate_never_outlives_the_ca(tmp_path):
    result = picokit.make_certs(str(tmp_path), ips=["10.0.0.1"], ca_days=100, server_days=5000, now=NOW)
    assert result["broker"].not_valid_after_utc == result["ca"].not_valid_after_utc


def test_keys_are_small_ec_keys_so_the_ca_pem_fits_the_board_easily(made):
    assert isinstance(made["ca"].public_key(), ec.EllipticCurvePublicKey)
    assert len(open(made["paths"]["ca.crt"], "rb").read()) < 1000
    assert len(open(made["paths"]["ca.crt"], "rb").read()) < picokit.MAX_CA_BYTES / 4


def test_the_files_are_written_and_the_keys_are_private(made):
    for name in ("ca.crt", "ca.key", "broker.crt", "broker.key", "mosquitto-tls.conf"):
        assert os.path.getsize(made["paths"][name]) > 0
    if os.name == "posix":
        for name in ("ca.key", "broker.key"):
            assert stat.S_IMODE(os.stat(made["paths"][name]).st_mode) == 0o600


def test_no_private_key_ends_up_in_a_file_meant_for_the_board_or_broker_config(made):
    for name in ("ca.crt", "broker.crt", "mosquitto-tls.conf"):
        assert b"PRIVATE KEY" not in open(made["paths"][name], "rb").read()


def test_the_mosquitto_snippet_points_at_the_broker_files(made):
    conf = open(made["paths"]["mosquitto-tls.conf"]).read()
    assert "listener 8883" in conf and "broker.crt" in conf and "broker.key" in conf
    assert "ca.key" not in conf.replace("Keep ca.key", "")


# --- independent proof they work -----------------------------------------------------

@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_openssl_accepts_the_chain_for_each_name_and_rejects_others(made):
    p = made["paths"]

    def verify(*extra):
        return subprocess.run(["openssl", "verify", "-CAfile", p["ca.crt"], *extra, p["broker.crt"]],
                              capture_output=True, text=True).returncode

    assert verify() == 0
    assert verify("-verify_ip", "192.168.0.10") == 0
    assert verify("-verify_ip", "127.0.0.1") == 0
    assert verify("-verify_hostname", "broker.home.lan") == 0
    assert verify("-verify_ip", "192.168.0.11") != 0
    assert verify("-verify_hostname", "other.home.lan") != 0


def handshake(server_cert, server_key, client_trusts, connect_name):
    """A real TLS handshake on localhost; returns True if the client accepted the server."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(server_cert, server_key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        try:
            conn, _ = listener.accept()
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except (ssl.SSLError, OSError):
            pass
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    client_ctx = ssl.create_default_context(cafile=client_trusts)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with client_ctx.wrap_socket(raw, server_hostname=connect_name) as tls:
                tls.sendall(b"x")
        return True
    except (ssl.SSLError, OSError):
        return False
    finally:
        thread.join(5)


def test_a_client_connecting_by_ip_accepts_the_broker_certificate(made):
    p = made["paths"]
    assert handshake(p["broker.crt"], p["broker.key"], p["ca.crt"], "127.0.0.1") is True


def test_a_client_rejects_a_name_that_is_not_in_the_certificate(made):
    p = made["paths"]
    assert handshake(p["broker.crt"], p["broker.key"], p["ca.crt"], "localhost") is False


def test_a_client_trusting_a_different_ca_rejects_the_broker(made, tmp_path):
    other = picokit.make_certs(str(tmp_path / "other"), ips=["127.0.0.1"], now=NOW)
    p = made["paths"]
    assert handshake(p["broker.crt"], p["broker.key"], other["paths"]["ca.crt"], "127.0.0.1") is False


# --- reuse, renewal, refusing to clobber -----------------------------------------------

def test_a_second_run_reuses_the_ca_byte_for_byte_so_the_board_needs_nothing(made, tmp_path):
    out = os.path.dirname(made["paths"]["ca.crt"])
    before = (open(made["paths"]["ca.crt"], "rb").read(), open(made["paths"]["ca.key"], "rb").read())
    again = picokit.make_certs(out, ips=["10.9.8.7"], force=True, now=NOW)
    assert again["ca_reused"] is True
    assert (open(again["paths"]["ca.crt"], "rb").read(), open(again["paths"]["ca.key"], "rb").read()) == before
    assert san(again["broker"])[1] == ["10.9.8.7"]
    again["ca"].public_key().verify(again["broker"].signature, again["broker"].tbs_certificate_bytes,
                                    ec.ECDSA(hashes.SHA256()))


def test_existing_broker_files_are_not_replaced_without_force(made):
    out = os.path.dirname(made["paths"]["ca.crt"])
    before = open(made["paths"]["broker.key"], "rb").read()
    with pytest.raises(picokit.ToolError, match="--force"):
        picokit.make_certs(out, ips=["10.9.8.7"], now=NOW)
    assert open(made["paths"]["broker.key"], "rb").read() == before


def test_only_half_a_ca_is_refused_not_guessed_at(made):
    out = os.path.dirname(made["paths"]["ca.crt"])
    os.remove(made["paths"]["ca.key"])
    with pytest.raises(picokit.ToolError, match="only one of ca.crt and ca.key"):
        picokit.make_certs(out, ips=["10.9.8.7"], force=True, now=NOW)


def test_a_ca_cert_and_key_that_do_not_match_are_refused(made, tmp_path):
    out = os.path.dirname(made["paths"]["ca.crt"])
    other = picokit.make_certs(str(tmp_path / "other"), ips=["10.0.0.1"], now=NOW)
    shutil.copy(other["paths"]["ca.key"], made["paths"]["ca.key"])
    with pytest.raises(picokit.ToolError, match="do not belong together"):
        picokit.make_certs(out, ips=["10.9.8.7"], force=True, now=NOW)


def test_a_ca_about_to_expire_is_not_silently_reused(tmp_path):
    out = str(tmp_path)
    picokit.make_certs(out, ips=["10.0.0.1"], ca_days=10, now=NOW)
    with pytest.raises(picokit.ToolError, match="expires within"):
        picokit.make_certs(out, ips=["10.0.0.2"], force=True, now=NOW)


# --- input checking -----------------------------------------------------------------------

def test_an_address_is_required():
    with pytest.raises(picokit.ToolError, match="at least one --ip or --host"):
        picokit.parse_names([], [])


@pytest.mark.parametrize("host", ["", "-bad.example", "bad host", "a_b.example", "x" * 300, "bad/host", "https://x"])
def test_bad_host_names_are_rejected(host):
    with pytest.raises(picokit.ToolError, match="not a valid host name"):
        picokit.parse_names([host], [])


@pytest.mark.parametrize("ip", ["", "999.1.1.1", "192.168.0", "broker", "1.2.3.4/24"])
def test_bad_ip_addresses_are_rejected(ip):
    with pytest.raises(picokit.ToolError, match="not a valid IP"):
        picokit.parse_names([], [ip])


def test_ipv6_addresses_are_accepted():
    assert [str(a) for a in picokit.parse_names([], ["fd00::1"])[1]] == ["fd00::1"]


# --- command line ---------------------------------------------------------------------------

def test_the_command_line_makes_certificates_and_explains_the_next_steps(tmp_path, capsys):
    code = picokit.main(["make-certs", "--ip", "192.168.0.10", "--out", str(tmp_path / "c")])
    out = capsys.readouterr().out
    assert code == 0
    assert "SHA-256 fingerprint" in out and "install-ca" in out and "Keep ca.key" in out


def test_command_line_errors_are_one_plain_line_not_a_traceback(tmp_path, capsys):
    assert picokit.main(["make-certs", "--out", str(tmp_path / "c")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err and err.count("\n") == 1
