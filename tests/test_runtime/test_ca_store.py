import pytest

from firmware.runtime import ca_store

CERT = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


@pytest.fixture(autouse=True)
def ca_path(tmp_path, monkeypatch):
    path = tmp_path / "ca.pem"
    monkeypatch.setattr(ca_store, "CA_PATH", str(path))
    return path


def test_default_path_is_in_the_root_of_the_board():
    import importlib

    assert importlib.reload(ca_store).CA_PATH == "/ca.pem"


def test_missing_file_means_not_installed(ca_path):
    assert ca_store.installed() is False
    with pytest.raises(ca_store.CaError, match="No CA certificate is installed"):
        ca_store.load()


def test_a_certificate_loads_unchanged(ca_path):
    ca_path.write_text(CERT)
    assert ca_store.load() == CERT
    assert ca_store.installed() is True


def test_several_certificates_in_one_file_are_allowed(ca_path):
    ca_path.write_text(CERT + CERT)
    assert ca_store.load() == CERT + CERT


@pytest.mark.parametrize("content,message", [
    ("", "not a PEM certificate"),
    ("hello", "not a PEM certificate"),
    ("-----BEGIN CERTIFICATE-----\nno end marker", "not a PEM certificate"),
    ("x" * (ca_store.MAX_CA_BYTES + 1), "too large"),
])
def test_unusable_files_are_rejected_with_a_reason(ca_path, content, message):
    ca_path.write_text(content)
    assert ca_store.installed() is False
    with pytest.raises(ca_store.CaError, match=message):
        ca_store.load()


@pytest.mark.parametrize("key_header", [
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----",
])
def test_a_private_key_is_never_accepted_even_next_to_a_certificate(ca_path, key_header):
    ca_path.write_text(CERT + key_header + "\nMIIE\n-----END PRIVATE KEY-----\n")
    assert ca_store.installed() is False
    with pytest.raises(ca_store.CaError, match="private key"):
        ca_store.load()


def test_binary_garbage_is_rejected_not_a_crash(ca_path):
    ca_path.write_bytes(b"\xff\xfe\x00\x80" * 10)
    assert ca_store.installed() is False


def test_a_directory_at_the_path_is_not_a_ca(ca_path):
    ca_path.mkdir()
    assert ca_store.installed() is False
