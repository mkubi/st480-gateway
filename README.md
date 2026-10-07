# st480-gateway

A self-made replacement for the TECH Ethernet module (ST-505) for boilers with a
**TECH ST-480** controller. It reads the controller's bus, publishes everything to
**MQTT** with Home Assistant style auto-discovery (Domoticz creates all devices by
itself, no `idx` juggling) and lets you change the pump mode and the CH / DHW
set-points remotely - from Domoticz, a wall-mounted kiosk page or Telegram.

Runs happily on a 2008 Asus Eee PC 901 (Atom, 1 GB RAM, 32-bit antiX Linux).
Hardware: a plain USB → RS-232 adapter and a 3-wire cable to the controller's RJ12
socket - see [docs/hardware.md](docs/hardware.md).

```
 TECH ST-480 ──bus 9600 8N1──> USB serial ──> st480_gw.py ──MQTT──> Mosquitto
      ^                                          │  ^                   │
      └──── commands after the controller's poll ┘  │          ┌───────┴────────┐
                                                    │          v                v
                                   cmd_st480.py (TCP, local)  Domoticz       st480_bot.py
                                                               │  frontpage     (Telegram
                                                               │  + kiosk        commands)
                                                               └─ dzVents ──> Telegram alerts
```

## Features

- Robust frame parser (byte stream, 4-byte aligned registers, CRC16/MCRF4XX,
  resynchronisation) - see [docs/protocol.md](docs/protocol.md)
- 20+ values: CH / DHW actual and set temperatures, flue gas, feeder, fan power,
  fuel level / reserve, pumps, feeder, fan, controller state incl. ~120 decoded
  state / alarm codes
- Remote control that is **verified on a real ST-480**: pump mode, CH set-point,
  DHW set-point (the controller beeps to confirm). Values are clamped to the limits
  the controller itself reports.
- Commands are sent right after the controller's poll frame, exactly like the
  original module; repeated clicks on a set-point are merged into one command
- Heating advisor: in manual operation "add fuel", "too much air", "switch DHW pump
  on / off" from the flue gas and water temperature trends (`ADV_*` thresholds);
  in automatic mode a summary - how long the boiler is in its current state
- MQTT auto-discovery (Domoticz, Home Assistant, openHAB, ...)
- Telegram: alerts (alarm, overheating, lost connection, DHW cold / hot) and a bot
  for `/stav`, `/uk 58`, `/tuv 50`, `/rezim letny`
- Kiosk mode for the laptop screen next to the boiler
- Boiler simulator and raw bus capture tool for testing without a boiler

> The code, log messages and device names are in **Slovak** (the project's origin);
> the documentation is in English. Device names can be changed in `ENTITIES`.

## Repository layout

| Path | Content |
|---|---|
| `gateway/st480_gw.py` | the gateway (serial <-> MQTT, TCP command port) |
| `gateway/cmd_st480.py` | command line client (`-c l|p|b|k`, `-w REG VALUE`) |
| `gateway/st480_sim.py` | boiler simulator on a virtual serial port |
| `bot/` | Telegram bot + config template |
| `domoticz/kotol_notifikacie.lua` | dzVents script with Telegram alerts |
| `domoticz/frontpage/` | settings and patch for the Domoticz "frontpage" |
| `system/` | runit services, Mosquitto config, kiosk setup |
| `tools/` | raw bus capture tool and a sample capture |
| `install.sh` | installer (packages, files, runit services) |
| `docs/` | hardware (wiring), protocol, installation, troubleshooting |

## Quick start

```bash
git clone https://github.com/<you>/st480-gateway.git
cd st480-gateway
sudo ./install.sh                                   # gateway + Mosquitto
sudo ./install.sh --bot --domoticz ~/dev-domoticz   # + Telegram bot and Domoticz service
```

Then add the hardware **"MQTT Auto Discovery Client Gateway with LAN interface"**
in Domoticz (`127.0.0.1:1883`, prefix `homeassistant`). Full step-by-step guide,
including rebuilding the whole machine from scratch:
[docs/installation.md](docs/installation.md).

Test without a boiler:

```bash
python3 ~/st480/st480_sim.py ~/st480/sample_capture.log      # window 1
python3 ~/st480/st480_gw.py --port /tmp/st480-sim --log -     # window 2
```

## Safety

This talks to a solid fuel boiler. Only registers that were verified on a real
controller are writable (`ALLOWED_WRITES` in the gateway). Do not add more without
testing on a **cold** boiler and never control the CH pump remotely - it protects
the boiler from overheating. Use at your own risk.

## Credits

- Protocol research by the community in the elektroda.pl thread
  ["Protokół komunikacyjny sterowników TECH"](https://www.elektroda.pl/rtvforum/topic2689981.html)
- Register dictionary: [dzien.net/tech](https://dzien.net/tech/index.php?baza=slownik)
- The Domoticz frontpage itself is a third-party project without a license notice,
  so only our settings and a patch are included here.

## License

MIT, see [LICENSE](LICENSE).
