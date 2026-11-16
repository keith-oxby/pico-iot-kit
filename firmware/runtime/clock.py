"""Setting the clock over NTP.

The RP2040's RTC starts at 2021-01-01 on every power-up. Verified TLS checks
certificate dates against it, and a wrong clock makes every connection fail
("validity starts in the future"; confirmed on a Pico W), so the clock must
be set first. Encrypted-but-unverified and plaintext connections don't look
at dates and don't need this, which keeps them working on a LAN with no
internet access.
"""

import time

from firmware import watchdog

MIN_VALID_YEAR = 2025  # anything earlier means the RTC was never set
MAX_ATTEMPTS = 5
MAX_BACKOFF_S = 8


def is_set():
    return time.localtime()[0] >= MIN_VALID_YEAR


def sync(sleep=watchdog.nap):
    """Make sure the clock is set, asking NTP (with backoff) if it isn't.

    Returns immediately if the clock already looks right. Raises OSError if
    NTP can't be reached, which the runtime treats like any other connection
    failure: log it, back off, retry.
    """
    if is_set():
        return
    import ntptime

    delay = 1
    for attempt in range(MAX_ATTEMPTS):
        try:
            ntptime.settime()
            if is_set():
                return
        except Exception:
            pass
        if attempt < MAX_ATTEMPTS - 1:
            sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)
    raise OSError("Could not set the clock over NTP.")
