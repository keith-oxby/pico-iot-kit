# Third-party notices

pico-iot-kit itself is MIT licensed (see `LICENSE`).

## The firmware image (`.uf2`)

A built image contains MicroPython and the libraries it is built from (the
Raspberry Pi Pico SDK, the CYW43 Wi-Fi driver, lwIP, Mbed TLS, TinyUSB,
littlefs, FatFs, the maths library, newlib and libgcc, and micropython-lib
packages). Their copyright notices and licence texts are written into
**`THIRD_PARTY_LICENSES.txt`** at build time (`build/licenses.py`) and attached
to every release next to the `.uf2`; redistribute the two together. The build
fails if the image ever contains a component that has no entry there.

Two things worth knowing:

- **Bluetooth is switched off in the image** (`build/make_board.py`). The stock
  Pico W firmware includes BlueKitchen's BTstack, which may be used for
  non-commercial purposes only. Nothing here uses Bluetooth, so it is left
  out, and the build fails if it is ever linked back in.
- **The Wi-Fi chip driver is used under Raspberry Pi's licence** (`LICENSE.RP`
  in cyw43-driver), which permits use and redistribution only together with
  Raspberry Pi semiconductor devices. The image is for the Pico W and is not
  for other hardware. The Wi-Fi chip's own firmware blob is part of the image.
  It ships inside cyw43-driver with no licence file of its own, so it is
  redistributed under the same `LICENSE.RP` terms. The blob originates from
  Infineon (formerly Cypress), whose own terms are not published with it and
  are not reproduced here.

This is a description of what the upstream licences ask for, not legal advice.

## Source files vendored in this repository

The following third-party software is included in the repository unmodified.

## umqtt.simple (MQTT client for MicroPython)

- **Files:** `vendor/umqtt/simple.py`
- **Source:** https://github.com/micropython/micropython-lib, path
  `micropython/umqtt.simple/umqtt/simple.py`
- **Pinned commit:** `a7fe5c39f39bb52ae3e520ab5c1554c62dcddc29`
  ("umqtt.simple: Compact CONNACK error path", 2026-09-11)
- **SHA-256 of the vendored file:** `5458b97fc074db01263e82ae898727aee2a15079ea5cff9d2f6d6abbea8ea217`
  (checked by `tests/test_vendor.py`, so any edit is noticed)
- **Originally written by:** Paul Sokolovsky
- **Modifications:** none
- **Licence:** MIT. micropython-lib's `LICENSE` states that files not under
  a module-specific licence are MIT, and the `umqtt.simple` directory
  contains no other licence file. The relevant text from that `LICENSE`:

```
The MIT License (MIT)

Copyright (c) 2013, 2014 micropython-lib contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## How it is used

On a dev board, copy it to `/lib/umqtt/simple.py`
(`mpremote mkdir :lib; mpremote mkdir :lib/umqtt; mpremote cp vendor/umqtt/simple.py :lib/umqtt/simple.py`).
The frozen build (Sprint 4) is expected to take it from micropython-lib with
`require("umqtt.simple")` instead, so the licence travels with it.
