import hashlib
import re

VENDORED = "vendor/umqtt/simple.py"


def test_vendored_umqtt_matches_the_hash_recorded_in_the_notices():
    notices = open("THIRD_PARTY_NOTICES.md").read()
    recorded = re.search(r"SHA-256 of the vendored file:\*\* `([0-9a-f]{64})`", notices).group(1)
    actual = hashlib.sha256(open(VENDORED, "rb").read()).hexdigest()
    assert actual == recorded, "vendor/umqtt/simple.py changed; vendored code must stay unmodified"


def test_the_notices_carry_the_licence_text_and_source():
    notices = open("THIRD_PARTY_NOTICES.md").read()
    assert "Copyright (c) 2013, 2014 micropython-lib contributors" in notices
    assert "Permission is hereby granted, free of charge" in notices
    assert "micropython/micropython-lib" in notices
    assert re.search(r"Pinned commit:\*\* `[0-9a-f]{40}`", notices)


def test_the_vendored_client_supports_what_the_adapters_need():
    source = open(VENDORED).read()
    for needed in ("def set_last_will", "def check_msg", "def ping", "def publish", "def subscribe"):
        assert needed in source
    # the TLS path used by firmware/adapters/tls.py: a context passed as `ssl`
    assert "self.ssl.wrap_socket(self.sock, server_hostname=self.server)" in source
