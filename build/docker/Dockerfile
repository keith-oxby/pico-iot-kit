# Build environment for the frozen pico-iot-kit firmware (checklist item 9).
# MicroPython is pinned by tag; its pico-sdk, cyw43, lwIP, mbedTLS and
# micropython-lib submodules come from that tag, so the image is the one place
# a version changes. Build it from the repository root:
#   podman build -t pico-iot-kit-build build/docker
FROM docker.io/library/debian:bookworm-slim

ARG MICROPYTHON_TAG=v1.29.0

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git python3 ca-certificates \
        gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${MICROPYTHON_TAG}" \
        https://github.com/micropython/micropython /opt/micropython \
    && cd /opt/micropython \
    && make -C ports/rp2 BOARD=RPI_PICO_W submodules \
    && make -C mpy-cross

# The Pico SDK downloads and builds picotool the first time it is needed. Do
# that now, into a fixed cache, so later builds need no network (and the
# version is the one this SDK requires). A throwaway build warms the cache.
ENV PICOTOOL_FETCH_FROM_GIT_PATH=/opt/picotool-cache
RUN make -C /opt/micropython/ports/rp2 BOARD=RPI_PICO_W BUILD=/tmp/warm -j"$(nproc)" \
    && rm -rf /tmp/warm \
    && ls /opt/picotool-cache

WORKDIR /work
