# TECH ST-480 bus protocol

What we know from captures on a real ST-480 (controller type `15A7 = 0x0015`,
`16FF = 0x0006`), the elektroda.pl thread and the dzien.net dictionary.
Everything marked **verified** was tested on the author's boiler.

## Physical layer

- 9600 baud, 8N1, controller talks continuously
- USB serial adapters deliver data in ~16 ms chunks (latency timer), so frames
  arrive split at random places. **Never parse with `readline()`** - `0x0A` is
  a normal data byte. Buffer bytes and cut complete frames.

## Frame format

```
02 26 DD DD | RR RR VV VV | RR RR VV VV | ... | 02 18 CC CC
```

| Part | Meaning |
|---|---|
| `02 26` | start of frame |
| `DD DD` | **destination** address (see below) |
| `RR RR VV VV` | register and value, big-endian, 16 bit each |
| `02 18 CC CC` | end "register"; its value is the CRC |

Everything is aligned to 4 bytes, so the end marker is only searched at aligned
positions (a value of `0x0218` inside the data does not break parsing).

**CRC:** CRC-16/MCRF4XX (reflected polynomial `0x8408`, init `0xFFFF`, no final
XOR) over all bytes **before** `02 18`, transmitted big-endian.

```python
def crc16_mcrf4xx(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc
```

### Addresses

| Address | Device |
|---|---|
| `FFF4` | Ethernet module (ST-505) - **the gateway plays this role** |
| `FFFA` | room regulator (e.g. ST-280) |
| `FFF8` | GSM module |
| `0000` | all devices - commands are sent with this address |

## Timing

The controller sends one frame every ~4 s in a 16 s cycle:

| Frame | Content |
|---|---|
| A | main status: state, CH/DHW temperatures and set-points, pumps, fan, mode, clock, limits |
| C | flue gas and feeder temperature, controller type |
| B | fuel level / reserve, table `16F9`/`16C2` (valve data) |
| poll | empty frame `02 26 FF F4 02 18 A1 DE` - invitation for the Ethernet module |

A command is sent **after the poll frame** (the gateway waits 50 ms). According to
the elektroda.pl thread the controller leaves ~6 s for an answer. One command per
poll; the gateway merges pending writes to the same register.

## Value encoding

- all values are 16-bit, **signed** (two's complement) where temperatures can be negative
- measured temperatures are in 0.1 °C (`0x010E` = 27.0 °C), set-points in whole °C
- `0xF830` = -200.0 °C means **sensor missing / broken**
- limits (`169E`, `169F`): low byte = minimum, high byte = maximum (`0x501E` = 30-80 °C)
- clock (`1620`, `0298`): high byte = hour, low byte = minute (`0x150E` = 21:14)

## Registers sent by the ST-480 (read)

| Reg | Meaning | Notes |
|---|---|---|
| `157C` | controller state | codes in `STATES` in the gateway (`0002` operation, `0021` off, `001F` ignition, `0052`/`0082` supervision, `0006`/`001E` manual, alarms ...) |
| `157D` | CH measured | /10 |
| `157E` | CH set-point | °C |
| `166E` | DHW measured | /10 |
| `1616` | DHW set-point | °C |
| `1681` | outside temperature | /10, signed, `F830` = no sensor |
| `15B7` | flue gas temperature | /10, signed |
| `16F8` | feeder temperature | /10 |
| `159B` | fan power | % |
| `1587` / `1588` | feeder / fan | 0 / 1 |
| `1589` / `158B` | CH pump / DHW pump | 0 / 1 |
| `15CD` | pump mode | 0 house heating, 1 DHW priority, 2 parallel, 3 summer |
| `16F1` | fuel level | /512 in the original code (dictionary says %) |
| `16F2` | fuel reserve | hours (updated every 5 min) |
| `169E` / `169F` | CH / DHW set-point limits | min/max bytes |
| `1620` / `1621` | controller clock / weekday | hh:mm, 0 = Sunday |
| `15A7` / `16FF` | controller type | `0015` / `0006` = ST-480 |
| `01F6` / `028E` | CH / DHW set-point "from regulator" | `0` when nobody sets it |
| `0298` / `0299` | regulator clock / weekday | |
| `0245` | pump mode command register | |
| `16F9` / `16C2` | parameter index / valve address | table, not decoded |
| `1684` | unknown | always 0 so far |

## Registers you can write

| Reg | Meaning | Values | Status |
|---|---|---|---|
| `0245` | pump mode | 0-3 (as `15CD`) | **verified** |
| `01F6` | CH set-point | within `169E` limits | **verified** (controller beeps) |
| `028E` | DHW set-point | within `169F` limits | **verified** (controller beeps) |
| `0288` | standby | 0 / 1 | untested candidate (dictionary) |

Frame example - set CH to 58 °C: `02 26 00 00 01 F6 00 3A 02 18 94 22`

### Tested and NOT working on the ST-480

| Reg | Expected function |
|---|---|
| `157E` / `1616` | set-point written to the status register |
| `02FC` | "change of CH set-point" |
| `0209` / `020A` | ignition / extinguishing |
| `01FE` | manual operation on/off |
| `01FF` / `0200` / `0201` / `0203` | feeder / fan / CH pump / DHW pump (also in manual mode set on the panel) |
| `1587` / `1588` / `1589` / `158B` | writing the status registers of feeder / fan / pumps |

Pumps, fan and feeder can only be switched on the controller's panel. Emulating the
room regulator (`01F9` active + `0311` measured / `0312` set room temperature) might
influence the CH pump in automatic mode - not tested.

## Firmware

TECH update files (ST-505, Wi-Fi RS, ST-280) share one container:
4-byte little-endian length, encrypted payload (block cipher without chaining -
repeated 16-byte blocks), 16-byte trailer. Nothing about the protocol can be learned
from them without the key stored in the devices. Sniffing a real module with
`tools/st480_capture.py` is the practical way to learn new commands.
