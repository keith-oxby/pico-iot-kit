# ruff: noqa: F821  (include, require and freeze come from MicroPython's manifest runner)
# MicroPython frozen-module manifest for pico-iot-kit (checklist item 9).
#
# build/stage.py fills $PICOKIT_STAGE with the application; everything in it
# is frozen. Used through FROZEN_MANIFEST by build/inner-build.sh.
import os

# The board's own manifest: asyncio, dht, onewire, networking (ntptime, ssl),
# the set the firmware was developed and tested against.
include("$(BOARD_DIR)/manifest.py")

# MQTT client. Dev boards use the vendored copy in vendor/umqtt instead.
require("umqtt.simple")

# Freeze exactly these files, in sorted order. Freezing the directory would visit
# them in the filesystem's order, which differs from machine to machine and
# reorders the frozen data, so the same source would build different images.
stage = os.environ["PICOKIT_STAGE"]
scripts = []
for root, dirs, files in os.walk(stage):
    dirs.sort()
    scripts += [os.path.relpath(os.path.join(root, f), stage) for f in sorted(files) if f.endswith(".py")]
freeze(stage, tuple(scripts))
