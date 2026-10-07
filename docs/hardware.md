# Hardware

The whole connection is an ordinary **USB → RS-232 adapter** and a simple cable.
No level shifter, no extra components.

| Part | What the author uses |
|---|---|
| computer | Asus Eee PC 901 (any Linux machine works) |
| adapter | USB → RS-232 with DB9 connector, chip **FTDI FT232** (`0403:6001`) |
| cable | DIY, 3 wires (made from an old mouse cable), DB9 female ↔ RJ12 plug |
| controller | TECH ST-480, RJ12 socket for the Ethernet module / room regulator |

## Why plain RS-232 works

As measured by BartekElektro1 in the elektroda.pl thread
([post #25](https://www.elektroda.pl/rtvforum/topic2689981.html), ST-37 RS) the
controller transmits asynchronous serial data at about **0 V idle / +15 V active**,
i.e. inverted compared to TTL UART. That is exactly the polarity an RS-232 receiver
expects (≈0 V = mark / idle, positive = space), so a normal RS-232 adapter reads it
directly. A 3.3 V / 5 V TTL adapter would need an inverter and level shifting.

Serial settings: **9600 baud, 8N1**, no flow control.

## Wiring (verified on ST-480)

RJ12 has 6 pins, but only 4 signals - two pairs are connected together inside the
controller:

| RJ12 pin | Signal | DB9 pin (adapter) |
|---|---|---|
| 1 | controller **RX** (≈8 V idle) | **3** (TXD) |
| 2 | GND | - (same as pin 4) |
| 3 | controller **TX** | **2** (RXD) |
| 4 | GND | **5** (GND) |
| 5, 6 | **≈15 V supply** for modules | **do not connect!** |

Pin numbers as seen by the author looking at the front of the RJ12 plug with the
latch on top. The ST-37 RS has the same pinout (`x G T G y y` in post #25); other
TECH controllers may differ.

### Check before connecting

Because RJ12 numbering conventions differ, verify with a multimeter instead of
trusting the numbers (controller powered, plug disconnected from the adapter):

- **GND**: the two pins with continuity to each other and to the controller ground
- **supply**: the two pins with ≈15 V (with ripple) against GND - never connect them
- **controller RX**: ≈8 V idle against GND
- **controller TX**: ≈0 V idle, pulses up to ≈15 V while the controller transmits

Then connect controller TX → adapter RXD (DB9 pin 2), controller RX → adapter TXD
(DB9 pin 3), GND → DB9 pin 5.

### Cable colours

A typical RJ12 cable (sikor16, latch **down**, pins from the left): 1 white = controller
RX, 2 black = GND, 3 red = controller TX, 4 green = GND, 5 yellow and 6 blue = +14.65 V.
Colours depend on the cable - always measure.

## Microcontroller instead of RS-232 (ESP8266 / ESP32)

sikor16 (elektroda.pl post #391) runs the same protocol on a Wemos D1 mini with
ESPHome. Notes from his build:

- the controller's TX needs an **inverter** in front of the 3.3 V UART input
  (e.g. BC547 transistor) - the signal is 0 V / +15 V with RS-232 polarity
- the controller's RX is internally pulled up to ≈7.5 V and accepts commands only
  from a **clean open collector**: NPN transistor (BC547) pulling the line to GND,
  **no pull-up and no series resistor**. With 1 kΩ in the collector the low level was
  ≈1.1 V and the controller ignored commands; an extra 2.2 kΩ pull-up to 5 V also
  disturbed it
- on ESP8266 UART0 is used for the controller, so the logger must be disabled
  (`logger: baud_rate: 0`)

His schematics and ESPHome code are in the thread. The solar controller ST-401n uses
different levels (≈+6.5 V idle, ≈-7 V bits, TTL polarity) and was read through a
MAX3232 module.

## Linux side

- FTDI is supported by the kernel (`ftdi_sio`), no driver needed
- user must be in group `dialout` (Debian/antiX) or `uucp` (Arch)
- use the stable path instead of `/dev/ttyUSB0`, e.g.
  `/dev/serial/by-id/usb-FTDI_USB_Serial_Converter_<serial>-if00-port0`,
  set it as `SERIAL_PORT` in `st480_gw.py`
- the FTDI latency timer (16 ms) splits frames into chunks - the gateway's parser
  handles that (never use `readline()`)
