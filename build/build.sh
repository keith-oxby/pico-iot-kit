#!/usr/bin/env bash
# Builds a frozen pico-iot-kit .uf2 (checklist item 9, docs/architecture.md
# section 9) in the pinned container under build/docker/.
#
#   build/build.sh [--board RPI_PICO_W] [--version X.Y.Z] [--rebuild-image]
#
# Output: build/out/pico-iot-kit-picow-<version>.uf2 (version defaults to the
# git tag, or "dev"). Needs podman or docker. Only the Pico W is built for
# releases; other boards are untested.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
board=RPI_PICO_W
version=""
rebuild=0

while [ $# -gt 0 ]; do
  case "$1" in
    --board) board="${2:?--board needs a value}"; shift 2 ;;
    --version) version="${2:?--version needs a value}"; shift 2 ;;
    --rebuild-image) rebuild=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$version" ]; then
  version="$(git -C "$repo" describe --tags --exact-match 2>/dev/null || echo dev)"
  version="${version#v}"
fi
case "$board" in
  RPI_PICO_W) short=picow ;;
  *) echo "board $board is not supported (only RPI_PICO_W is built and tested)" >&2; exit 2 ;;
esac

# PICOKIT_ENGINE=docker|podman forces one; otherwise podman, then docker.
if [ -n "${PICOKIT_ENGINE:-}" ]; then engine="$PICOKIT_ENGINE"
elif command -v podman >/dev/null 2>&1; then engine=podman
elif command -v docker >/dev/null 2>&1; then engine=docker
else echo "build.sh needs podman or docker" >&2; exit 1; fi
command -v "$engine" >/dev/null 2>&1 || { echo "$engine not found" >&2; exit 1; }

image=pico-iot-kit-build
if [ "$rebuild" = 1 ] || ! "$engine" image inspect "$image" >/dev/null 2>&1; then
  "$engine" build -t "$image" "$here/docker"
fi

out="$here/out"
mkdir -p "$out"
uf2="pico-iot-kit-$short-$version.uf2"

# Root inside the container: the source tree in the image is root-owned, and
# the build writes next to it. SELinux labelling is disabled for the mounts
# so the repository's own labels are left alone.
"$engine" run --rm --user 0:0 --security-opt label=disable \
  -v "$repo":/work:ro -v "$out":/out \
  -e BOARD="$board" -e UF2_NAME="$uf2" -e PICOKIT_VERSION="$version" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  "$image" sh /work/build/inner-build.sh

echo "built $out/$uf2"
