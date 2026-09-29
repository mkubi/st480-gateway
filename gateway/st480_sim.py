#!/usr/bin/env python3
"""
Simulator kotla ST480 na testovanie na stole.

Prehrava zaznam st480_raw.log dookola na virtualny seriovy port
/tmp/st480-sim (v realnom tempe, vratane vyziev) a vypisuje prikazy,
ktore mu posle gateway.

Pouzitie:
    python3 st480_sim.py st480_raw.log
    python3 st480_gw.py --port /tmp/st480-sim --log -     (v druhom okne)
"""
import os
import pty
import sys
import threading
import time
import tty

LINK = "/tmp/st480-sim"

path = sys.argv[1] if len(sys.argv) > 1 else "st480_raw.log"
records = [(float(t), bytes.fromhex(h))
           for t, h in (line.split() for line in open(path) if line.strip())]

master, slave = pty.openpty()
tty.setraw(master)
tty.setraw(slave)
if os.path.lexists(LINK):
    os.unlink(LINK)
os.symlink(os.ttyname(slave), LINK)
print(f"Virtualny port: {LINK} -> {os.ttyname(slave)}", flush=True)
print(f"Prehravam {len(records)} kuskov dat dookola, Ctrl+C = koniec", flush=True)


def reader():
    while True:
        data = os.read(master, 256)
        print(time.strftime("%H:%M:%S"), "prikaz z gateway:", data.hex(), flush=True)


threading.Thread(target=reader, daemon=True).start()

try:
    while True:
        t0, start = records[0][0], time.monotonic()
        for ts, data in records:
            delay = ts - t0 - (time.monotonic() - start)
            if delay > 0:
                time.sleep(delay)
            os.write(master, data)
        time.sleep(4)
except KeyboardInterrupt:
    os.unlink(LINK)
