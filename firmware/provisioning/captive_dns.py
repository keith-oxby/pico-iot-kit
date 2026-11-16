"""Minimal DNS responder for the setup AP.

Phones only run their captive-portal check (and open the setup page) if DNS
works on the network, so while in SETUP mode every A query is answered with
the device's own address. Pure function over bytes; ap_portal.py owns the
UDP socket.
"""

_TTL = b"\x00\x00\x00\x3c"  # 60 s


def build_response(query, ip="192.168.4.1"):
    """Answer a DNS query packet (bytes) with `ip`, or return None.

    A queries get the address; other types get an empty (NODATA) answer.
    Malformed packets, responses, non-standard opcodes and multi-question
    queries are ignored.
    """
    if len(query) < 17:
        return None
    flags = (query[2] << 8) | query[3]
    if flags & 0x8000 or (flags >> 11) & 0x0F != 0:
        return None
    if (query[4] << 8) | query[5] != 1:
        return None

    i = 12
    while True:
        if i >= len(query):
            return None
        length = query[i]
        if length == 0:
            i += 1
            break
        if length & 0xC0:  # compression pointers aren't valid in a question
            return None
        i += 1 + length
    if i + 4 > len(query):
        return None
    question = query[12 : i + 4]
    qtype = (query[i] << 8) | query[i + 1]
    qclass = (query[i + 2] << 8) | query[i + 3]

    answer = b""
    if qtype == 1 and qclass == 1:
        answer = (
            b"\xc0\x0c\x00\x01\x00\x01"
            + _TTL
            + b"\x00\x04"
            + bytes(int(part) for part in ip.split("."))
        )
    header = query[0:2] + b"\x81\x80\x00\x01" + (b"\x00\x01" if answer else b"\x00\x00") + b"\x00\x00\x00\x00"
    return header + question + answer
