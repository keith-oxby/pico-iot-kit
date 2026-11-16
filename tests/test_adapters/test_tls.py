import sys

import pytest

from firmware.adapters import tls
from firmware.runtime import ca_store

CERT = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


class FakeContext:
    def __init__(self, protocol):
        self.protocol = protocol
        self.verify_mode = None
        self.loaded = []

    def load_verify_locations(self, cadata=None):
        self.loaded.append(cadata)


class FakeSsl:
    PROTOCOL_TLS_CLIENT = "client"
    CERT_NONE = 0
    CERT_REQUIRED = 2
    SSLContext = FakeContext


@pytest.fixture
def ssl_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "ssl", FakeSsl)
    return FakeSsl


@pytest.fixture
def ca_path(tmp_path, monkeypatch):
    path = tmp_path / "ca.pem"
    monkeypatch.setattr(ca_store, "CA_PATH", str(path))
    return path


# --- mode selection -----------------------------------------------------------

@pytest.mark.parametrize("config,mode", [
    ({}, tls.VERIFIED),                                      # secure by default
    ({"use_tls": True}, tls.VERIFIED),
    ({"use_tls": True, "tls_verify": True}, tls.VERIFIED),
    ({"use_tls": True, "tls_verify": False}, tls.UNVERIFIED),
    ({"use_tls": False}, tls.PLAINTEXT),
    ({"use_tls": False, "tls_verify": True}, tls.PLAINTEXT),
    ({"use_tls": False, "tls_verify": False}, tls.PLAINTEXT),
])
def test_mode_for(config, mode):
    assert tls.mode_for(config) == mode


def test_only_verified_mode_needs_the_clock():
    assert tls.needs_clock(tls.VERIFIED) is True
    assert tls.needs_clock(tls.UNVERIFIED) is False
    assert tls.needs_clock(tls.PLAINTEXT) is False


def test_every_mode_has_a_log_label_and_the_weak_ones_say_so():
    assert set(tls.LABELS) == {tls.VERIFIED, tls.UNVERIFIED, tls.PLAINTEXT}
    assert "NOT" in tls.LABELS[tls.UNVERIFIED]
    assert "NOT" in tls.LABELS[tls.PLAINTEXT]


# --- contexts -------------------------------------------------------------------

def test_plaintext_has_no_context_and_does_not_even_import_ssl(monkeypatch):
    monkeypatch.delitem(sys.modules, "ssl", raising=False)
    assert tls.make_context(tls.PLAINTEXT) is None


def test_verified_requires_a_certificate_and_trusts_the_installed_ca(ssl_module, ca_path):
    ca_path.write_text(CERT)
    ctx = tls.make_context(tls.VERIFIED)
    assert ctx.protocol == "client"
    assert ctx.verify_mode == ssl_module.CERT_REQUIRED
    assert ctx.loaded == [CERT]


def test_verified_without_a_ca_refuses_and_says_what_to_do(ssl_module, ca_path):
    with pytest.raises(tls.TlsConfigError) as exc:
        tls.make_context(tls.VERIFIED)
    message = str(exc.value)
    assert "No CA certificate is installed" in message
    assert "README" in message and "another security mode" in message


@pytest.mark.parametrize("content,expected", [
    ("garbage", "not a PEM certificate"),
    (CERT + "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n", "private key"),
])
def test_verified_with_a_bad_ca_file_refuses_with_the_reason(ssl_module, ca_path, content, expected):
    ca_path.write_text(content)
    with pytest.raises(tls.TlsConfigError, match=expected):
        tls.make_context(tls.VERIFIED)


def test_verified_never_falls_back_to_a_weaker_context(ssl_module, ca_path, monkeypatch):
    created = []
    monkeypatch.setattr(FakeSsl, "SSLContext", lambda p: created.append(p) or FakeContext(p))
    with pytest.raises(tls.TlsConfigError):
        tls.make_context(tls.VERIFIED)
    assert created == []  # no context was even built without a CA


def test_unverified_encrypts_without_checking_and_ignores_the_ca(ssl_module, ca_path):
    ctx = tls.make_context(tls.UNVERIFIED)  # no CA installed, still fine
    assert ctx.verify_mode == ssl_module.CERT_NONE
    assert ctx.loaded == []


def test_unknown_modes_are_rejected(ssl_module):
    with pytest.raises(ValueError):
        tls.make_context("whatever")
