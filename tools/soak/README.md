# Soak test kit

Leaves one board running for a day or more against a test broker, and records
what happens: every reading, any gap between readings, offline events, resets,
watchdog warnings and the free heap. It is for finding slow problems (a leak, a
Wi-Fi chip that wedges, a stall) that a few minutes of testing cannot show.

## Setup (once)

On the machine that stays on (a Raspberry Pi works well; wired Ethernet, a
fixed address, podman, Python 3 with `pyserial`, and ports 1883 and 8883 open):

1. Make a broker certificate for **that machine's address** with the CA that is
   installed on the board, on the PC that holds `ca.key`:
   `python tools/picokit.py make-certs --ip <address> --out <dir containing ca.crt and ca.key>`.
   Copy only `broker.crt` and `broker.key` to `tools/test-broker/certs/` on the
   soak machine. **Never copy `ca.key` there.**
2. Copy `tools/test-broker/broker.sh`, `tools/test-broker/mosquitto.conf` and
   `tools/soak/soak.py` over, and start the broker:
   `tools/test-broker/broker.sh up`.
3. Plug the board into the soak machine's USB port. Set the board up through
   its phone form with the soak machine's address as the broker host.

## Run

```
tmux new -s soak
python3 tools/soak/soak.py --serial /dev/ttyACM0 --out ~/soak-logs
```

Detach with Ctrl-B then D. Read `~/soak-logs/soak-summary.txt` (rewritten every
minute) and `~/soak-logs/soak-events.log` at any time.

**Never connect to the board with `mpremote` (or anything that resets it) while
it runs.** The firmware's watchdog would reset the board about 8 seconds later.
The logger reads the serial port passively; do the same.

## What to look for

A good soak: no gaps longer than 1.6 poll intervals, one boot seen, no
watchdog warnings, no stall resets, no crashes, no offline events other than
ones you caused, and a free heap that stays roughly flat (it should not trend
down). Anything else is a finding: the events log has the time and the line
from the board.
