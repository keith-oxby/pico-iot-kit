#!/usr/bin/env sh
# Runs INSIDE the build container (started by build/build.sh): stages the
# application, builds MicroPython with it frozen, and copies out the .uf2.
#
#   /work   the repository (read-only)
#   /out    output directory (writable)
# Environment: BOARD, UF2_NAME, PICOKIT_VERSION, HOST_UID, HOST_GID (who should own the output).
set -eu

: "${BOARD:?}" "${UF2_NAME:?}" "${HOST_UID:?}" "${HOST_GID:?}"
MPY=/opt/micropython
STAGE="${PICOKIT_STAGE_DIR:-/out/stage}"  # the override is for checking the build is filesystem-independent
BUILD=/out/build-$BOARD

BOARD_DIR=/out/board-$BOARD

rm -rf "$STAGE" "$BUILD"
mkdir -p "$STAGE"
python3 /work/build/stage.py "$STAGE" /work > /out/staged-files.txt
# the stock board with Bluetooth off (see build/make_board.py for why)
python3 /work/build/make_board.py "$MPY/ports/rp2/boards/$BOARD" "$BOARD_DIR" > /dev/null

PICOKIT_STAGE="$STAGE" make -C "$MPY/ports/rp2" \
    BOARD="$BOARD" BOARD_DIR="$BOARD_DIR" BUILD="$BUILD" \
    FROZEN_MANIFEST=/work/build/manifest.py \
    -j"$(nproc)"

# the image must not contain BTstack (non-commercial licence); fail if it does
if grep -q "/lib/btstack/" "$BUILD/firmware.elf.map"; then
    echo "error: BTstack is linked into the image; Bluetooth was not switched off" >&2
    exit 1
fi

PICOKIT_VERSION="${PICOKIT_VERSION:-dev}" python3 /work/build/licenses.py \
    "$MPY" /work "$BUILD/firmware.elf.map" /out/THIRD_PARTY_LICENSES.txt

cp "$BUILD/firmware.uf2" "/out/$UF2_NAME"
# the container runs as root; hand the output back so it can be deleted
chown -R "$HOST_UID:$HOST_GID" /out
ls -l "/out/$UF2_NAME"
