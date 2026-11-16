#!/usr/bin/env bash
# Test Mosquitto for pico-iot-kit, run in podman: plaintext on 1883, TLS on 8883.
#
#   tools/test-broker/broker.sh up [--ip ADDR]   make certificates (first time) and start
#   tools/test-broker/broker.sh down             stop and remove the container
#   tools/test-broker/broker.sh logs             follow the broker log
#   tools/test-broker/broker.sh sub [TOPIC]      subscribe (default '#'), plaintext
#   tools/test-broker/broker.sh pub TOPIC MSG    publish, plaintext
#   tools/test-broker/broker.sh ca               print the path of the CA to install
#   tools/test-broker/broker.sh ha-up | ha-down  Home Assistant on :8123 (config in ha-config/)
#
# Certificates and keys live in tools/test-broker/certs/ (git-ignored). TEST ONLY:
# the broker accepts anonymous clients.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
certs="$here/certs"
name=pico-test-broker
image=docker.io/library/eclipse-mosquitto:2
ha_name=pico-test-ha
ha_image=ghcr.io/home-assistant/home-assistant:stable

default_ip() { ip -4 route get 1.1.1.1 | awk '{for (i=1;i<NF;i++) if ($i=="src") print $(i+1)}'; }

cmd="${1:-}"; shift || true
case "$cmd" in
  up)
    ip="$(default_ip)"
    if [ "${1:-}" = "--ip" ]; then ip="${2:?--ip needs an address}"; fi
    if [ ! -f "$certs/broker.crt" ]; then
      py="$repo/.venv/bin/python"; [ -x "$py" ] || py=python3
      "$py" "$repo/tools/picokit.py" make-certs --ip "$ip" --out "$certs"
    fi
    podman rm -f "$name" >/dev/null 2>&1 || true
    podman run -d --name "$name" --userns=keep-id --user "$(id -u):$(id -g)" \
      -p 1883:1883 -p 8883:8883 \
      -v "$here/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro,Z" \
      -v "$certs:/mosquitto/certs:ro,Z" "$image"
    echo "Broker up on $ip (1883 plaintext, 8883 TLS). CA: $certs/ca.crt"
    ;;
  down) podman rm -f "$name" ;;
  logs) podman logs -f "$name" ;;
  sub)  podman exec "$name" mosquitto_sub -h 127.0.0.1 -v -t "${1:-#}" ;;
  pub)  podman exec "$name" mosquitto_pub -h 127.0.0.1 -t "${1:?topic}" -m "${2:?message}" ;;
  ha-up)
    mkdir -p "$here/ha-config"
    podman rm -f "$ha_name" >/dev/null 2>&1 || true
    podman run -d --name "$ha_name" -p 8123:8123 -e TZ=UTC \
      -v "$here/ha-config:/config:Z" "$ha_image"
    echo "Home Assistant starting on http://localhost:8123 (first start takes a minute or two)"
    ;;
  ha-down) podman rm -f "$ha_name" ;;
  ca)   echo "$certs/ca.crt" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
