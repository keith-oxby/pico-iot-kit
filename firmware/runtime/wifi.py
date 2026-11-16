"""Station-mode Wi-Fi for RUN mode: joins the home network from config.

`network` is imported inside the functions so this module (and the runtime
package) can be imported on the host, where it doesn't exist.
"""

from firmware import watchdog

CONNECT_TIMEOUT_S = 20


class WifiError(Exception):
    pass


def connect(ssid, password, timeout_s=CONNECT_TIMEOUT_S, sleep=watchdog.nap):
    """Join the given network, returning once an IP address is assigned.

    Returns immediately if already connected. Raises WifiError on a
    definite failure (wrong password, no such AP) or on timeout. An empty
    password means an open network.
    """
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return
    if password:
        wlan.connect(ssid, password)
    else:
        wlan.connect(ssid)

    waited = 0
    while not wlan.isconnected():
        if wlan.status() < 0:  # negative statuses are failures on the cyw43 driver
            raise WifiError("wifi connect failed (status %d)" % wlan.status())
        if waited >= timeout_s:
            raise WifiError("wifi connect timed out")
        sleep(1)
        waited += 1
