#!/usr/bin/env python3
"""Soak logger for pico-iot-kit: runs next to the test broker for hours or days.

    python3 tools/soak/soak.py --serial /dev/ttyACM0

Watches two things and writes what it sees to a directory (default
./soak-logs):

- the MQTT side: every reading from the board, gaps between readings, and the
  retained online/offline status (the last-will "offline" means the board
  dropped without saying goodbye);
- the board's USB serial output, read passively, never with mpremote (which
  would soft-reset the board and leave the watchdog to reset it): boots,
  watchdog warnings, stall resets, connect failures, crashes and the free heap.

Files: soak-events.log (one line per notable event), soak-summary.txt
(rewritten every minute and at exit). Stop with Ctrl-C.

Needs pyserial and the test broker (tools/test-broker/broker.sh up) running in
podman. The MQTT side talks to the broker through `podman exec ... mosquitto_sub`
so nothing else has to be installed.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import threading
import time

GAP_FACTOR = 1.6  # a gap is a silence of more than 1.6 intervals
HEAP_RE = re.compile(r"heap free (\d+)")
SERIAL_EVENTS = (
    ("watchdog: armed", "boot"),
    ("watchdog: no check-in", "watchdog warning"),
    ("no connection for", "stall reset"),
    ("app: crashed", "crash"),
    ("Traceback", "traceback"),
    ("connect failed", "connect failed"),
    ("connection lost", "connection lost"),
    ("read failed", "read failed"),
)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


class GapTracker:
    """Counts messages per topic and flags silences longer than the interval allows."""

    def __init__(self, interval_s, now=time.monotonic):
        self.limit = interval_s * GAP_FACTOR
        self.now = now
        self.last = {}
        self.counts = {}
        self.gaps = []  # (topic, seconds)
        self.max_gap = {}

    def message(self, topic):
        t = self.now()
        self.counts[topic] = self.counts.get(topic, 0) + 1
        found = None
        if topic in self.last:
            gap = t - self.last[topic]
            self.max_gap[topic] = max(self.max_gap.get(topic, 0), gap)
            if gap > self.limit:
                found = gap
                self.gaps.append((topic, gap))
        self.last[topic] = t
        return found

    def silent_for(self, topic):
        return self.now() - self.last[topic] if topic in self.last else None


def classify_serial_line(line):
    """Return (event_label, heap_bytes_or_None) for a line of board output."""
    heap = HEAP_RE.search(line)
    label = None
    for needle, name in SERIAL_EVENTS:
        if needle in line:
            label = name
            break
    return label, int(heap.group(1)) if heap else None


class Soak:
    def __init__(self, out_dir, interval_s, now=time.monotonic, clock=utc_now):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.events = open(os.path.join(out_dir, "soak-events.log"), "a", buffering=1)
        self.tracker = GapTracker(interval_s, now)
        self.started = clock()
        self.clock = clock
        self.lock = threading.Lock()
        self.counters = {}
        self.heaps = []
        self.status = None

    def event(self, text):
        with self.lock:
            line = "%s %s" % (self.clock().strftime("%Y-%m-%d %H:%M:%S"), text)
            self.events.write(line + "\n")
            print(line, flush=True)

    def count(self, name):
        with self.lock:
            self.counters[name] = self.counters.get(name, 0) + 1

    # -- MQTT -------------------------------------------------------------
    def mqtt_line(self, line):
        parts = line.rstrip("\n").split("\t", 2)
        if len(parts) != 3:
            return
        retained, topic, payload = parts
        if topic.endswith("/status"):
            if payload != self.status:
                self.event("status %s%s" % (payload, " (retained)" if retained == "1" else ""))
                if payload == "offline":
                    self.count("offline")
            self.status = payload
            return
        if retained == "1" or topic.startswith("homeassistant/"):
            return
        gap = self.tracker.message(topic)
        if gap:
            self.count("gaps")
            self.event("GAP %s: %.0f s between readings" % (topic, gap))

    # -- serial -----------------------------------------------------------
    def serial_line(self, line):
        line = line.strip()
        if not line:
            return
        label, heap = classify_serial_line(line)
        if heap is not None:
            with self.lock:
                self.heaps.append(heap)
        if label:
            self.count(label)
            if label != "connect failed" or self.counters.get("connect failed", 0) <= 3:
                self.event("serial [%s] %s" % (label, line[:160]))

    # -- summary ----------------------------------------------------------
    def summary(self):
        with self.lock:
            up = self.clock() - self.started
            lines = ["soak started %s UTC, running %s" % (
                self.started.strftime("%Y-%m-%d %H:%M:%S"), str(up).split(".")[0])]
            for topic in sorted(self.tracker.counts):
                lines.append("  %-40s %6d readings, longest gap %5.0f s, last %4.0f s ago" % (
                    topic, self.tracker.counts[topic], self.tracker.max_gap.get(topic, 0),
                    self.tracker.silent_for(topic) or 0))
            lines.append("  gaps: %d   offline events: %d   boots seen: %d   watchdog warnings: %d   "
                         "stall resets: %d   crashes: %d" % tuple(
                             self.counters.get(k, 0) for k in
                             ("gaps", "offline", "boot", "watchdog warning", "stall reset", "crash")))
            if self.heaps:
                lines.append("  free heap (bytes): first %d, min %d, max %d, last %d, samples %d" % (
                    self.heaps[0], min(self.heaps), max(self.heaps), self.heaps[-1], len(self.heaps)))
            else:
                lines.append("  free heap: no samples yet")
            return "\n".join(lines) + "\n"

    def write_summary(self):
        text = self.summary()
        with open(os.path.join(self.out_dir, "soak-summary.txt"), "w") as f:
            f.write(text)
        return text


def run_mqtt(soak, container):
    cmd = ["podman", "exec", container, "mosquitto_sub", "-h", "127.0.0.1", "-v",
           "-F", "%r\\t%t\\t%p", "-t", "+/#"]
    while True:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
        for line in proc.stdout:
            soak.mqtt_line(line)
        soak.event("mqtt listener ended (%s); restarting in 5 s" % proc.poll())
        time.sleep(5)


def run_serial(soak, port):
    import serial

    ser = None
    buf = b""
    while True:
        try:
            if ser is None:
                ser = serial.Serial(port, 115200, timeout=1)
                soak.event("serial port %s opened" % port)
            buf += ser.read(4096)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                soak.serial_line(raw.decode(errors="replace"))
        except (serial.SerialException, OSError):
            if ser is not None:
                soak.event("serial port lost (board reset or unplugged); retrying")
            ser = None
            time.sleep(1)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--serial", help="serial port of the board (omit to watch MQTT only)")
    p.add_argument("--container", default="pico-test-broker")
    p.add_argument("--interval", type=float, default=30, help="the board's poll interval in seconds")
    p.add_argument("--out", default="soak-logs")
    args = p.parse_args(argv)

    soak = Soak(args.out, args.interval)
    soak.event("soak logger started (interval %g s, serial %s)" % (args.interval, args.serial or "off"))
    threading.Thread(target=run_mqtt, args=(soak, args.container), daemon=True).start()
    if args.serial:
        threading.Thread(target=run_serial, args=(soak, args.serial), daemon=True).start()
    try:
        while True:
            time.sleep(60)
            soak.write_summary()
    except KeyboardInterrupt:
        print(soak.write_summary())
        return 0


if __name__ == "__main__":
    sys.exit(main())
