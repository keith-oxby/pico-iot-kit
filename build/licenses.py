"""Writes THIRD_PARTY_LICENSES.txt for the firmware image (Sprint 4, item 4g).

A distributed .uf2 contains other people's code, and their licences ask for
their copyright notices and licence texts to travel with it. This collects
them from the pinned source tree at build time, so they cannot go stale, and
refuses to produce a notice if the image contains a component it doesn't
know about (add the licence here first). Not legal advice: it follows what the
upstream licences say.

    python3 build/licenses.py MICROPYTHON_DIR PROJECT_DIR LINK_MAP OUTPUT
"""

import os
import re
import sys

# lib/<dir> in the MicroPython tree -> the notice entry that covers it
COVERED_LIB_DIRS = {
    "cyw43-driver": "cyw43-driver",
    "libm": "libm",
    "littlefs": "littlefs",
    "lwip": "lwIP",
    "mbedtls": "Mbed TLS",
    "mbedtls_errors": "Mbed TLS",
    "oofatfs": "ooFatFs",
    "pico-sdk": "Raspberry Pi Pico SDK",
    "tinyusb": "TinyUSB",
}
# lib dirs that must NOT be in the image (licence incompatible with our MIT release)
FORBIDDEN_LIB_DIRS = {
    "btstack": "BTstack allows non-commercial use only; the board is built without Bluetooth",
}

# (name, licence as it applies to us, [(label, path or ("header", path, ...))], note)
ENTRIES = [
    ("MicroPython", "MIT", [("LICENSE", "LICENSE")], None),
    ("micropython-lib (umqtt.simple, ntptime, ssl, mip, requests, webrepl, ...)", "MIT",
     [("LICENSE", "lib/micropython-lib/LICENSE")], None),
    ("Raspberry Pi Pico SDK", "BSD-3-Clause", [("LICENSE.TXT", "lib/pico-sdk/LICENSE.TXT")], None),
    ("cyw43-driver (Wi-Fi chip driver)", "Raspberry Pi licence (LICENSE.RP)",
     [("LICENSE.RP", "lib/cyw43-driver/LICENSE.RP")],
     "This software is dual-licensed. The image is for the Raspberry Pi Pico W (an RP2040 "
     "device) and relies on the LICENSE.RP terms above, which allow use and redistribution "
     "only together with Raspberry Pi semiconductor devices. The alternative terms in the "
     "driver's LICENSE file (non-commercial use) are not relied on."),
    ("lwIP", "BSD-3-Clause", [("COPYING", "lib/lwip/COPYING")], None),
    ("Mbed TLS", "Apache-2.0 (chosen from Apache-2.0 OR GPL-2.0-or-later)",
     [("LICENSE", "lib/mbedtls/LICENSE")], None),
    ("TinyUSB", "MIT", [("LICENSE", "lib/tinyusb/LICENSE")], None),
    ("littlefs", "BSD-3-Clause",
     [("copyright lines from the source header, with the standard licence text",
       ("bsd3", "lib/littlefs/lfs2.h"))], None),
    ("ooFatFs (FatFs by ChaN)", "FatFs licence (one condition)",
     [("source header", ("header", "lib/oofatfs/ff.c", "ChaN"))], None),
    ("libm (maths functions from newlib-nano / fdlibm)", "BSD-style and MIT notices, below",
     [("source headers", ("headers", "lib/libm"))], None),
]
OWN = [("pico-iot-kit", "MIT", [("LICENSE", ("project", "LICENSE"))], None)]

# The Wi-Fi chip's own firmware is built in (lib/cyw43-driver/firmware). It ships
# inside cyw43-driver with no licence file of its own, so the driver's terms apply.
UNVERIFIED = (
    "The Wi-Fi chip firmware (the CYW43439 patch and CLM blobs from "
    "lib/cyw43-driver/firmware) is also part of the image. It is distributed inside "
    "cyw43-driver, which publishes no separate licence text for it, so it is "
    "redistributed here under the driver's LICENSE.RP terms above. The blobs "
    "originate from Infineon (formerly Cypress); Infineon's own terms are not "
    "published with them and are not reproduced here."
)

RULE = "=" * 78

# The standard BSD-3-Clause conditions (SPDX "BSD-3-Clause"), for components whose
# source headers only carry the copyright lines and the SPDX identifier.
BSD3_BODY = """Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

NOT_IN_IMAGE = (
    "Bluetooth is switched off in this image (build/make_board.py), so it does not contain "
    "BTstack, aioble or the Bluetooth chip firmware. The licence files reproduced above "
    "(micropython-lib's in particular) describe whole source repositories, and name "
    "packages that are not part of this image."
)


# Archives the toolchain links in (newlib's C and maths libraries, libgcc).
KNOWN_ARCHIVES = {"libg.a", "libm.a", "libgcc.a"}
TOOLCHAIN_ENTRIES = [
    ("newlib (C library, newlib-nano build) and its maths library", "BSD-style and other notices, below",
     [("Debian copyright file", ("toolchain", "usr/share/doc/libnewlib-arm-none-eabi/copyright"))], None),
    ("libgcc (compiler runtime)", "GPL-3.0-or-later with the GCC Runtime Library Exception",
     [("Debian copyright file", ("toolchain", "usr/share/doc/gcc-12-base/copyright"))],
     "libgcc is distributed under the GCC Runtime Library Exception (reproduced in the text "
     "above), which allows it to be linked into and distributed with a program like this "
     "firmware without the program being covered by the GPL."),
]


def lib_dirs_in_map(map_text, micropython_dir="/opt/micropython"):
    """The lib/<dir> components of MicroPython's tree that appear in the link map."""
    prefix = micropython_dir.rstrip("/") + "/lib/"
    return set(re.findall(re.escape(prefix) + r"([A-Za-z0-9_.+-]+)/", map_text))


def archives_in_map(map_text):
    return set(re.findall(r"/([A-Za-z0-9_+-]+\.a)\(", map_text))


def check_components(map_text, micropython_dir="/opt/micropython"):
    """Raise if the image contains something we don't cover or mustn't ship."""
    extra = sorted(archives_in_map(map_text) - KNOWN_ARCHIVES)
    if extra:
        raise ValueError("the image links %s with no licence entry; add it to "
                         "build/licenses.py" % ", ".join(extra))
    found = lib_dirs_in_map(map_text, micropython_dir)
    for name in sorted(found & set(FORBIDDEN_LIB_DIRS)):
        raise ValueError("lib/%s is in the image: %s" % (name, FORBIDDEN_LIB_DIRS[name]))
    unknown = sorted(found - set(COVERED_LIB_DIRS) - {"micropython-lib"})
    if unknown:
        raise ValueError("the image contains lib/%s with no licence entry; add it to "
                         "build/licenses.py" % ", lib/".join(unknown))
    return found


def _comment_blocks(text, limit_lines=80):
    head = "\n".join(text.split("\n")[:limit_lines])
    return re.findall(r"/\*.*?\*/", head, re.S)


def leading_comment(path, must_contain=None):
    """The first header comment (that mentions `must_contain`, if given)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        blocks = _comment_blocks(f.read())
    for block in blocks:
        if must_contain is None or must_contain in block:
            return block
    raise ValueError("%s has no header comment%s to reproduce" % (
        path, " mentioning %r" % must_contain if must_contain else ""))


def copyright_lines(path):
    """The 'Copyright ...' lines of a source header, plus its licence identifier."""
    block = leading_comment(path, "Copyright")
    lines = [re.sub(r"^[\s/*]+", "", line).rstrip() for line in block.split("\n")]
    return [line for line in lines if line.startswith(("Copyright", "SPDX-License-Identifier"))]


def libm_notices(directory):
    """Every distinct copyright block in the libm sources except MicroPython's own."""
    seen = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".c", ".h")):
            continue
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as f:
            for block in _comment_blocks(f.read(), 120):
                body = " ".join(block.split())
                if not re.search(r"copyright|permission to use", body, re.I):
                    continue
                if "This file is part of the MicroPython project" in body:
                    continue
                if body not in [" ".join(b.split()) for b in seen]:
                    seen.append(block)
    if not seen:
        raise ValueError("no libm copyright notices found in %s" % directory)
    return "\n\n".join(seen)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().rstrip("\n")


def build_notice(micropython_dir, project_dir, map_text, version, toolchain_root="/"):
    check_components(map_text, micropython_dir)
    parts = [
        "THIRD-PARTY LICENCES for the pico-iot-kit firmware image (%s)" % version,
        "",
        "The .uf2 contains the components below. Each is covered by the licence text "
        "reproduced under its heading.",
        "",
    ]
    for name, licence, sources, note in OWN + ENTRIES + TOOLCHAIN_ENTRIES:
        parts += [RULE, "%s -- %s" % (name, licence), RULE, ""]
        for label, source in sources:
            if isinstance(source, tuple):
                kind, *rest = source
                rel = rest[0]
                if kind == "header":
                    rel = (rest[0], rest[1] if len(rest) > 1 else None)
                if kind == "project":
                    text = read_text(os.path.join(project_dir, rel))
                elif kind == "toolchain":
                    text = read_text(os.path.join(toolchain_root, rel))
                elif kind == "header":
                    text = leading_comment(os.path.join(micropython_dir, rel[0]), rel[1])
                elif kind == "bsd3":
                    text = "\n".join(copyright_lines(os.path.join(micropython_dir, rel))) + "\n\n" + BSD3_BODY
                else:
                    text = libm_notices(os.path.join(micropython_dir, rel))
            else:
                text = read_text(os.path.join(micropython_dir, source))
            parts += ["[%s]" % label, text, ""]
        if note:
            parts += ["Note: " + note, ""]
    parts += [RULE, "Not included, or not reproduced", RULE, "", NOT_IN_IMAGE, "", UNVERIFIED, ""]
    return "\n".join(parts)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    mpy, project, link_map, output = sys.argv[1:]
    with open(link_map, encoding="utf-8", errors="replace") as f:
        map_text = f.read()
    version = os.environ.get("PICOKIT_VERSION", "dev")
    with open(output, "w", encoding="utf-8") as f:
        f.write(build_notice(mpy, project, map_text, version))
    print("wrote", output)
