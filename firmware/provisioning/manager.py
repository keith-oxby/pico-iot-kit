"""ProvisioningManager: the boot-time state machine.

Decides between SETUP mode (no config, or BOOTSEL held 5s) and RUN mode
(handing off to firmware/runtime), per docs/architecture.md section 5.
Checklist item 4 (docs/architecture.md, section 11).

BOOTSEL reset: the RP2040 boot ROM enters the USB bootloader if BOOTSEL is
held *at reset*, so "hold during boot" cannot reach our code. Instead, for
BOOTSEL_WINDOW_S after the firmware starts the manager watches the button
(LED on = press now); once pressed it must stay held for
BOOTSEL_HOLD_SECONDS to clear the config. When the hold succeeds the LED
blinks three times so the user knows to let go; a missed press or a timeout
just turns the LED off. The cost is a BOOTSEL_WINDOW_S delay on every boot
that is not interrupted by a press.
"""

import time

from . import ap_portal, config_store

BOOTSEL_HOLD_SECONDS = 5
BOOTSEL_WINDOW_S = 3
RESTART_DELAY_S = 1  # lets the confirmation page finish sending
_POLL_S = 0.1
_CONFIRM_BLINKS = 3
_BLINK_S = 0.1


def _bootsel_reader():
    """Return a zero-argument function reporting BOOTSEL, or None if this
    firmware can't read it (in which case the reset feature is unavailable
    but the device still boots).
    """
    try:
        import rp2

        return rp2.bootsel_button
    except (ImportError, AttributeError):
        return None


def _led_setter():
    try:
        import machine

        pin = machine.Pin("LED", machine.Pin.OUT)
    except Exception:
        return lambda on: None
    return lambda on: pin.value(1 if on else 0)


def _confirm_reset(set_led, sleep):
    """Blink the LED to show the hold was accepted (it was on, ends on)."""
    for _ in range(_CONFIRM_BLINKS):
        set_led(False)
        sleep(_BLINK_S)
        set_led(True)
        sleep(_BLINK_S)


def bootsel_held_for_reset(read_button=None, sleep=time.sleep, set_led=None):
    """Return True if BOOTSEL was pressed within BOOTSEL_WINDOW_S and then
    held continuously for BOOTSEL_HOLD_SECONDS.
    """
    if read_button is None:
        read_button = _bootsel_reader()
        if read_button is None:
            return False
    if set_led is None:
        set_led = _led_setter()

    window_ticks = round(BOOTSEL_WINDOW_S / _POLL_S)
    hold_ticks = round(BOOTSEL_HOLD_SECONDS / _POLL_S)
    set_led(True)
    try:
        for _ in range(window_ticks):
            if read_button():
                for _ in range(hold_ticks):
                    if not read_button():
                        return False
                    sleep(_POLL_S)
                _confirm_reset(set_led, sleep)
                return True
            sleep(_POLL_S)
        return False
    finally:
        set_led(False)


def _restart():
    time.sleep(RESTART_DELAY_S)
    import machine

    machine.reset()


def start():
    """Entry point called first thing from firmware/app.py.

    Returns:
        the validated config dict if RUN mode should proceed. In SETUP mode
        this serves the portal until a config is saved and then restarts the
        device, so it does not return on real hardware.
    """
    if bootsel_held_for_reset():
        config_store.clear()

    config = config_store.load()
    if config is not None:
        return config

    # SETUP mode
    ap_portal.start_ap()
    ap_portal.serve_setup_portal(on_config_submitted=config_store.save)
    _restart()
    return None
