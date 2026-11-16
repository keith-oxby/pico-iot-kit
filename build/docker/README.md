# Build container

`Dockerfile` pins MicroPython by tag (`MICROPYTHON_TAG`; its Pico SDK, cyw43,
lwIP, Mbed TLS and micropython-lib submodules come from that tag) and warms
the picotool cache, so builds need no network once the image exists.

Do not build it by hand: `build/build.sh` (from the repository root) builds
the image on first use, then the Pico W `.uf2` into `build/out/`. To change a
version, edit the `ARG` in the `Dockerfile`. CI builds the same image in
`.github/workflows/release.yml`.
