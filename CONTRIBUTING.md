# Contributing to pico-iot-kit

Thanks for considering a contribution. The project's core design goal is
that provisioning, the application runtime, sensor drivers, and backend
adapters stay independent of each other (see `docs/architecture.md`
section 3) -- please keep changes scoped to the relevant layer.

## Most common contributions

- **Adding a sensor type** -- see `docs/adding-a-sensor.md`.
- **Adding a downstream backend** -- see `docs/adding-a-backend.md`.

Both should be possible without touching the provisioning or runtime
layers beyond registering your new module and adding it to the setup
form's selector.

## Development workflow

- For quick iteration, copy modified `.py` files to a dev board with
  `mpremote` rather than running the full frozen-`.uf2` build each time (see
  "Running on a dev board" below).
- Run `ruff check .` and the host-side test suite (`pytest tests/`) before
  opening a PR -- see `.github/workflows/ci.yml` for exactly what CI runs.
  Install both with `pip install -r requirements-dev.txt`.
- The frozen `.uf2` build (`build/build.sh`, run inside `build/docker/`)
  is reserved for releases and final end-to-end verification on real
  hardware.

## Running on a dev board

1. Put stock MicroPython on a Pico W: hold BOOTSEL while plugging it in and
   copy the `.uf2` from micropython.org to the drive that appears.
2. `pip install -r requirements-dev.txt` (includes `mpremote`).
3. Copy the code: `mpremote cp -r firmware :` then
   `mpremote cp firmware/main.py :main.py`. MicroPython only runs a
   `main.py` in the board's root, which is why that second copy is needed.
   (This replaces any `main.py` already on the board.)
4. Reset the board; `main.py` runs provisioning, then the runtime.

Getting back to the REPL: `mpremote connect <port>` sends Ctrl-C, which
interrupts the firmware wherever it is (the 3 s BOOTSEL window, the setup
portal, the sensor loop). The crash handler waits 30 s before resetting, so
there is time to interrupt a crashing device too.

`mpremote connect ...` also **soft-resets the board** by default, which
restarts the firmware. Use `mpremote resume ...` to talk to the board
without resetting it.

There is no `boot.py` on purpose; see `firmware/app.py` for why.

**The watchdog.** The firmware starts the hardware watchdog (see
`firmware/watchdog.py`) as soon as it boots. `mpremote` soft-resets the board,
which stops the watchdog's feeding timer but not the watchdog, so a board
running it **resets about 8 seconds after you connect**, which can cut off a
file copy. To work on a board over USB, turn it off first (one quick command;
the board resets once and then stays up), and turn it back on when you are
done:

```
mpremote exec "open('/no_wdt','w').close()"    # watchdog off
mpremote rm :no_wdt                             # watchdog on again
```

To watch what a running board prints without disturbing it, read the serial
port passively (for example `python -m serial.tools.miniterm /dev/ttyACM0
115200`); don't use `mpremote connect`.

On a board running a **frozen** build, the frozen `main.py` always runs (a
`main.py` on the filesystem is ignored), while a `firmware/` folder on the
filesystem shadows the frozen package. So copying files with `mpremote` still
works for trying a change, and deleting that folder returns the board to the
frozen code. Build the image with `build/build.sh`.

## Security-sensitive changes

Anything touching `firmware/provisioning/` (credential handling, the
setup AP, config storage) or transport security in `firmware/adapters/`
should be reviewed against `docs/security.md` before merging.

## Commit style

Please land logically separate pieces of work as separate commits or PRs
-- this keeps the provisioning/runtime/adapter separation visible in the
repo's history.
