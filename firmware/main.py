"""MicroPython main.py -- the device's only entry point.

Hands off to firmware/app.py, which runs provisioning (including the
BOOTSEL reset check and SETUP mode) and then the Application Runtime.
See app.py for why there is no boot.py.
"""

from firmware.app import main

main()
