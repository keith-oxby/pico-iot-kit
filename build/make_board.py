"""Derives the board definition we build: the stock one with Bluetooth off.

Nothing in pico-iot-kit uses Bluetooth, and the stock Pico W firmware includes
BlueKitchen's BTstack, whose licence allows non-commercial use only. Leaving
it out keeps the image we distribute free of that restriction, and saves flash
and attack surface. The result is derived from the stock board at build time
(not copied into the repository) so a MicroPython upgrade brings its changes
along, and it fails loudly if the stock files change shape, instead of quietly
shipping Bluetooth again.

    python3 build/make_board.py STOCK_BOARD_DIR OUT_BOARD_DIR
"""

import os
import shutil
import sys

# file -> the exact lines that enable Bluetooth in that file
REMOVE = {
    "mpconfigboard.cmake": [
        "set(MICROPY_PY_BLUETOOTH ON)",
        "set(MICROPY_BLUETOOTH_BTSTACK ON)",
        "set(MICROPY_PY_BLUETOOTH_CYW43 ON)",
    ],
    "manifest.py": [
        'require("aioble")',
    ],
}
NOTE = "# Bluetooth is deliberately off here (build/make_board.py)."

# Without Bluetooth, nothing else puts the Pico SDK's pico/cyw43_driver.h on the
# include path (in the stock build it arrives through the Bluetooth libraries),
# and the Wi-Fi bus driver includes it. Adding the SDK's whole include directory
# would also expose its cyw43_configport.h, which would shadow MicroPython's own,
# so just that one header is copied next to the board and that directory added.
SDK_HEADER = "lib/pico-sdk/src/rp2_common/pico_cyw43_driver/include/pico/cyw43_driver.h"
HEADER_DIR = "include"
CMAKE_EXTRA = """
# The Wi-Fi bus driver needs pico/cyw43_driver.h, which only the Bluetooth libraries add.
include_directories(${MICROPY_BOARD_DIR}/include)
"""


def strip_lines(text, lines, name):
    """Drop each of `lines` (matched exactly, once) from `text`."""
    out = text.split("\n")
    for line in lines:
        hits = [i for i, text_line in enumerate(out) if text_line.strip() == line]
        if len(hits) != 1:
            raise ValueError(
                "%s: expected exactly one %r line, found %d; the stock board changed, "
                "so build/make_board.py needs updating" % (name, line, len(hits)))
        del out[hits[0]]
    return "\n".join(out)


def make_board(stock_dir, out_dir, micropython_dir=None):
    """`micropython_dir` is the MicroPython checkout (default: found from the
    stock board's location, ports/rp2/boards/<board>)."""
    if micropython_dir is None:
        micropython_dir = os.path.abspath(os.path.join(stock_dir, "..", "..", "..", ".."))
    header = os.path.join(micropython_dir, SDK_HEADER)
    if not os.path.isfile(header):
        raise ValueError("%s not found; the Pico SDK layout changed, so build/make_board.py "
                         "needs updating" % SDK_HEADER)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    shutil.copytree(stock_dir, out_dir)
    os.makedirs(os.path.join(out_dir, HEADER_DIR, "pico"))
    shutil.copyfile(header, os.path.join(out_dir, HEADER_DIR, "pico", "cyw43_driver.h"))
    for name, lines in REMOVE.items():
        path = os.path.join(out_dir, name)
        with open(path) as f:
            text = f.read()
        text = strip_lines(text, lines, name).rstrip("\n") + "\n" + NOTE + "\n"
        if name == "mpconfigboard.cmake":
            text += CMAKE_EXTRA
        with open(path, "w") as f:
            f.write(text)
    return out_dir


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(make_board(sys.argv[1], sys.argv[2]))
