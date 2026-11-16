import pytest

from firmware.provisioning import captive_dns


def query(name="captive.apple.com", qtype=1, qclass=1, flags=0x0100, ident=0x1234, qdcount=1):
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return (
        ident.to_bytes(2, "big") + flags.to_bytes(2, "big") + qdcount.to_bytes(2, "big")
        + b"\x00\x00\x00\x00\x00\x00" + labels + qtype.to_bytes(2, "big") + qclass.to_bytes(2, "big")
    )


def test_a_query_is_answered_with_the_portal_address():
    q = query()
    r = captive_dns.build_response(q)
    assert r[:2] == b"\x12\x34"                      # same id
    assert r[2:4] == b"\x81\x80"                      # response, RD+RA
    assert r[4:6] == b"\x00\x01" and r[6:8] == b"\x00\x01"
    assert r[12:12 + len(q) - 12] == q[12:]           # question echoed
    assert r.endswith(bytes([192, 168, 4, 1]))
    assert r[-6:-4] == b"\x00\x04"                    # rdlength


def test_answer_uses_the_given_ip():
    assert captive_dns.build_response(query(), "10.1.2.3").endswith(bytes([10, 1, 2, 3]))


def test_aaaa_gets_an_empty_answer():
    r = captive_dns.build_response(query(qtype=28))
    assert r[6:8] == b"\x00\x00"
    assert len(r) == len(query(qtype=28))


@pytest.mark.parametrize("packet", [
    b"",
    b"\x00" * 11,
    query(flags=0x8180),           # already a response
    query(flags=0x1100),           # opcode != 0
    query(qdcount=0),
    query(qdcount=2),
    query()[:-3],                  # truncated question
    query()[:20],                  # name runs off the end
    query()[:12] + b"\xc0\x0c" + b"\x00\x01\x00\x01",  # compression pointer
])
def test_bad_packets_are_ignored(packet):
    assert captive_dns.build_response(packet) is None
