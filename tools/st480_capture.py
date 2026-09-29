#!/usr/bin/env python3
"""
Raw capture of the boiler bus: writes every received chunk with a timestamp
("<unix time> <hex>" per line). Stop the gateway first (sudo sv down st480-gw),
the serial port can only be read by one program at a time.

    python3 st480_capture.py [/dev/ttyUSB0] [capture.log]

The output can be replayed with  st480_gw.py --replay  or  st480_sim.py.
Useful for sniffing original TECH modules (ST-280, ST-505, Wi-Fi RS).
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
out = sys.argv[2] if len(sys.argv) > 2 else "st480_raw.log"

with serial.Serial(port, 9600, timeout=1) as s, open(out, "a") as f:
    print(f"Capturing {port} -> {out}, Ctrl+C to stop")
    while True:
        data = s.read(s.in_waiting or 1)
        if data:
            f.write(f"{time.time():.3f} {data.hex()}\n")
            f.flush()
