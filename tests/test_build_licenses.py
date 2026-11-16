"""build/licenses.py: third-party licence notices for the firmware image."""

import pytest

from build import licenses

MAP_OK = """
 .text  0x1000  0x40 CMakeFiles/firmware.dir/opt/micropython/lib/lwip/src/core/tcp.c.o
 .text  0x2000  0x40 CMakeFiles/firmware.dir/opt/micropython/lib/mbedtls/library/ssl_tls.c.o
 .text  0x3000  0x40 CMakeFiles/firmware.dir/opt/micropython/lib/pico-sdk/src/rp2_common/x.c.o
 .text  0x4000  0x40 /usr/lib/gcc/arm-none-eabi/12.2.1/thumb/v6-m/nofp/libgcc.a(_udivsi3.o)
 .text  0x5000  0x40 /usr/lib/arm-none-eabi/lib/thumb/v6-m/nofp/libg.a(memcpy.o)
"""


def test_a_covered_image_passes():
    assert licenses.check_components(MAP_OK) == {"lwip", "mbedtls", "pico-sdk"}


def test_bluetooth_in_the_image_is_refused():
    bad = MAP_OK + " .text 0x6000 0x40 CMakeFiles/firmware.dir/opt/micropython/lib/btstack/src/hci.c.o\n"
    with pytest.raises(ValueError, match="non-commercial"):
        licenses.check_components(bad)


def test_a_component_without_a_licence_entry_is_refused():
    bad = MAP_OK + " .text 0x6000 0x40 CMakeFiles/firmware.dir/opt/micropython/lib/newthing/x.c.o\n"
    with pytest.raises(ValueError, match="lib/newthing"):
        licenses.check_components(bad)


def test_an_unknown_linked_archive_is_refused():
    bad = MAP_OK + " .text 0x6000 0x40 /usr/lib/arm-none-eabi/lib/libstdc++.a(new.o)\n"
    with pytest.raises(ValueError, match="libstdc"):
        licenses.check_components(bad)


def test_other_micropython_checkouts_are_found_by_their_prefix():
    text = " .text 0x1 0x2 /build/mpy/lib/lwip/a.o\n .text 0x1 0x2 /opt/micropython/lib/tinyusb/b.o\n"
    assert licenses.lib_dirs_in_map(text, "/build/mpy") == {"lwip"}


HEADER = """/* This file is part of ooFatFs, a customised version of FatFs */

/* FatFs - R0.13c
 * Copyright (C) 2018, ChaN, all right reserved.
 * Redistribution and use are permitted provided that the following condition is met: ...
 */
#include <x.h>
"""


def test_the_header_block_asked_for_is_the_one_returned(tmp_path):
    f = tmp_path / "ff.c"
    f.write_text(HEADER)
    assert "customised" in licenses.leading_comment(str(f))
    assert "ChaN" in licenses.leading_comment(str(f), "ChaN")
    with pytest.raises(ValueError, match="mentioning"):
        licenses.leading_comment(str(f), "nobody")


def test_copyright_lines_keep_the_identifier(tmp_path):
    f = tmp_path / "lfs2.h"
    f.write_text("/*\n * The little filesystem\n *\n * Copyright (c) 2022, The littlefs authors.\n"
                 " * Copyright (c) 2017, Arm Limited. All rights reserved.\n"
                 " * SPDX-License-Identifier: BSD-3-Clause\n */\n")
    assert licenses.copyright_lines(str(f)) == [
        "Copyright (c) 2022, The littlefs authors.",
        "Copyright (c) 2017, Arm Limited. All rights reserved.",
        "SPDX-License-Identifier: BSD-3-Clause"]


@pytest.fixture
def tree(tmp_path):
    mpy = tmp_path / "mpy"
    for rel, text in {
        "LICENSE": "MicroPython MIT text",
        "lib/micropython-lib/LICENSE": "micropython-lib text",
        "lib/pico-sdk/LICENSE.TXT": "pico sdk text",
        "lib/cyw43-driver/LICENSE.RP": "RP only text",
        "lib/lwip/COPYING": "lwip text",
        "lib/mbedtls/LICENSE": "mbedtls text",
        "lib/tinyusb/LICENSE": "tinyusb text",
        "lib/littlefs/lfs2.h": "/*\n * Copyright (c) 2022, The littlefs authors.\n * SPDX-License-Identifier: BSD-3-Clause\n */\n",
        "lib/oofatfs/ff.c": HEADER,
        "lib/libm/kf_sin.c": "/* kf_sin.c\n * Copyright (C) 1993 by Sun Microsystems, Inc. Permission to use, copy... */\n",
        "lib/libm/math.c": "/* This file is part of the MicroPython project\n * Copyright (c) Damien */\n",
    }.items():
        p = mpy / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "LICENSE").write_text("pico-iot-kit MIT text")
    toolchain = tmp_path / "root"
    for rel in ("usr/share/doc/libnewlib-arm-none-eabi/copyright", "usr/share/doc/gcc-12-base/copyright"):
        p = toolchain / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("copyright of " + rel)
    return mpy, project, toolchain


def test_the_notice_names_every_component_and_carries_its_text(tree):
    mpy, project, toolchain = tree
    text = licenses.build_notice(str(mpy), str(project), MAP_OK.replace("/opt/micropython", str(mpy)),
                                 "1.2.3", str(toolchain))
    for needle in ("(1.2.3)", "pico-iot-kit MIT text", "MicroPython MIT text", "micropython-lib text",
                   "RP only text", "lwip text", "mbedtls text", "tinyusb text", "BSD-3-Clause",
                   "ChaN", "Sun Microsystems", "gcc-12-base/copyright", "libnewlib-arm-none-eabi/copyright",
                   "CYW43439"):
        assert needle in text, needle
    assert "Damien" not in text  # MicroPython's own file headers are covered by its LICENSE
    assert "Bluetooth is switched off" in text


def test_the_notice_is_refused_for_an_image_containing_bluetooth(tree):
    mpy, project, toolchain = tree
    bad = MAP_OK.replace("/opt/micropython", str(mpy)) + " .text 1 2 %s/lib/btstack/a.o\n" % mpy
    with pytest.raises(ValueError):
        licenses.build_notice(str(mpy), str(project), bad, "x", str(toolchain))


def test_a_missing_licence_file_fails_loudly(tree):
    mpy, project, toolchain = tree
    (mpy / "lib/lwip/COPYING").unlink()
    with pytest.raises(OSError):
        licenses.build_notice(str(mpy), str(project), MAP_OK.replace("/opt/micropython", str(mpy)),
                              "x", str(toolchain))
