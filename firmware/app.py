"""The device lifecycle: provisioning, then the runtime, with crash recovery.

Called from firmware/main.py. There is deliberately no boot.py: MicroPython
executes boot.py itself without registering it as a module, so a main.py that
did `import boot` ran it a second time (provisioning, and its BOOTSEL window,
twice per boot -- confirmed on a Pico W). Nothing user-level runs between
boot.py and main.py, so one entry point is equivalent and runs once.

Crash recovery: any unhandled Exception, in provisioning or the runtime, is
logged, then the device waits CRASH_RESTART_DELAY_S and resets, so a
headless device recovers by itself. The delay is a floor against reset
loops and also gives a developer time to interrupt from the REPL.
KeyboardInterrupt (Ctrl-C) and other BaseExceptions are never swallowed.
Checklist item 8 (docs/architecture.md, section 11).
"""

import sys

from firmware import watchdog

CRASH_RESTART_DELAY_S = 30


def _print_exception(exc):
    if hasattr(sys, "print_exception"):  # MicroPython
        sys.print_exception(exc)
    else:
        import traceback

        traceback.print_exception(exc)


def _reset():
    import machine

    machine.reset()


def main(start_provisioning=None, run=None, sleep=watchdog.nap, reset=_reset, start_watchdog=watchdog.start):
    """Run provisioning then the runtime; reset the device if either fails.

    The callables are injectable for tests; by default they are the real
    provisioning manager and runtime, imported lazily so this module can be
    imported on the host.
    """
    start_watchdog()
    try:
        if start_provisioning is None:
            from firmware.provisioning import start as start_provisioning
        if run is None:
            from firmware.runtime.device import run

        config = start_provisioning()
        if config is None:
            raise RuntimeError("provisioning returned no config")
        run(config)
    except Exception as exc:
        print("app: crashed, restarting in %ds" % CRASH_RESTART_DELAY_S)
        _print_exception(exc)
        sleep(CRASH_RESTART_DELAY_S)
        reset()
