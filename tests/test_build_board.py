"""build/make_board.py: the stock Pico W board with Bluetooth taken out."""

import pytest

from build import make_board

CMAKE = """# cmake file for Raspberry Pi Pico W

set(PICO_BOARD "pico_w")

set(MICROPY_PY_LWIP ON)
set(MICROPY_PY_NETWORK_CYW43 ON)

# Bluetooth
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_BLUETOOTH_BTSTACK ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)
"""
MANIFEST = """include("$(PORT_DIR)/boards/manifest.py")

require("bundle-networking")

# Bluetooth
require("aioble")
"""


@pytest.fixture
def stock(tmp_path):
    sdk = tmp_path / "mpy" / make_board.SDK_HEADER
    sdk.parent.mkdir(parents=True)
    sdk.write_text("// the SDK header\n")
    d = tmp_path / "mpy" / "ports" / "rp2" / "boards" / "RPI_PICO_W"
    d.mkdir(parents=True)
    (d / "mpconfigboard.cmake").write_text(CMAKE)
    (d / "manifest.py").write_text(MANIFEST)
    (d / "mpconfigboard.h").write_text("#define MICROPY_HW_BOARD_NAME \"Raspberry Pi Pico W\"\n")
    return d


def test_bluetooth_is_taken_out_and_everything_else_is_kept(stock, tmp_path):
    out = make_board.make_board(str(stock), str(tmp_path / "out"))
    cmake = open(out + "/mpconfigboard.cmake").read()
    manifest = open(out + "/manifest.py").read()
    assert "BLUETOOTH" not in cmake.replace("# Bluetooth is deliberately off", "")
    assert "set(MICROPY_PY_NETWORK_CYW43 ON)" in cmake and 'set(PICO_BOARD "pico_w")' in cmake
    assert "include_directories(${MICROPY_BOARD_DIR}/include)" in cmake
    assert open(out + "/include/pico/cyw43_driver.h").read() == "// the SDK header\n"
    assert not (tmp_path / "out" / "include" / "cyw43_configport.h").exists()  # only the one header
    assert "aioble" not in manifest and 'require("bundle-networking")' in manifest
    assert "Raspberry Pi Pico W" in open(out + "/mpconfigboard.h").read()  # untouched files are copied


def test_the_stock_board_is_left_alone(stock, tmp_path):
    make_board.make_board(str(stock), str(tmp_path / "out"))
    assert open(stock / "mpconfigboard.cmake").read() == CMAKE


def test_running_it_again_replaces_the_output(stock, tmp_path):
    out = str(tmp_path / "out")
    make_board.make_board(str(stock), out)
    make_board.make_board(str(stock), out)
    assert open(out + "/manifest.py").read().count("deliberately off") == 1


@pytest.mark.parametrize("name,old", [
    ("mpconfigboard.cmake", "set(MICROPY_BLUETOOTH_BTSTACK ON)\n"),
    ("manifest.py", 'require("aioble")\n'),
])
def test_it_refuses_a_stock_board_that_changed_shape(stock, tmp_path, name, old):
    path = stock / name
    path.write_text(path.read_text().replace(old, ""))
    with pytest.raises(ValueError, match="stock board changed"):
        make_board.make_board(str(stock), str(tmp_path / "out"))


def test_a_duplicated_line_is_also_refused(stock, tmp_path):
    (stock / "manifest.py").write_text(MANIFEST + 'require("aioble")\n')
    with pytest.raises(ValueError):
        make_board.make_board(str(stock), str(tmp_path / "out"))


def test_it_refuses_a_missing_sdk_header(stock, tmp_path):
    (tmp_path / "mpy" / make_board.SDK_HEADER).unlink()
    with pytest.raises(ValueError, match="Pico SDK layout changed"):
        make_board.make_board(str(stock), str(tmp_path / "out"))
