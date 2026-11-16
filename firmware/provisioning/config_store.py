"""Reads and writes the validated config.json to the Pico's internal flash
filesystem.

Writes are atomic (write to a temp file, then rename) so a power loss
mid-write can't corrupt the active config. Checklist item 3
(docs/architecture.md, section 11).

NOTE (docs/architecture.md, section 8): secrets are currently stored as
plaintext JSON. This module's interface is deliberately narrow (exists /
load / save / clear) so an encrypted-at-rest backend can be swapped in
later (e.g. for Pico 2 / RP2350) without changing callers.
"""

import json
import os

from firmware.runtime.config_schema import ConfigValidationError, validate

CONFIG_PATH = "/config.json"
TMP_PATH = "/config.json.tmp"


def exists():
    try:
        with open(CONFIG_PATH):
            return True
    except OSError:
        return False


def load():
    """Load and validate the saved config. Returns None if no config
    exists, or if it exists but fails validation (caller should treat
    this the same as "no config" and fall back to SETUP mode).
    """
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
        return validate(config)
    except (ValueError, OSError, ConfigValidationError):
        return None


def save(config):
    """Validate and atomically write a new config.

    Raises ConfigValidationError if invalid; the existing config is left
    untouched on any failure.
    """
    validate(config)
    try:
        with open(TMP_PATH, "w") as f:
            json.dump(config, f)
        os.rename(TMP_PATH, CONFIG_PATH)
    except BaseException:
        _remove(TMP_PATH)
        raise


def clear():
    """Delete the saved config, forcing SETUP mode on next boot."""
    _remove(CONFIG_PATH)
    _remove(TMP_PATH)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
