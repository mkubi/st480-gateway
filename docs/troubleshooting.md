# Troubleshooting - lessons learned

Problems we actually hit while building this, with the fix.

## Gateway / protocol

| Symptom | Cause | Fix |
|---|---|---|
| Frames sometimes "single line", sometimes "multi line", random bad values | `readline()` on binary data; USB adapter delivers 16 ms chunks | byte buffer parser (current gateway) |
| Negative outside temperature never shown | value read as unsigned | signed int16 (`s16()`) |
| `-200 °C` | controller reports a missing / broken sensor (`0xF830`) | check sensor and cable; the gateway drops the value |
| Flue gas jumps to 600 °C, then -200 / 0 | intermittent contact: damaged insulation at the cable joint (wires touching = low / -200, loose joint = very high). The sensor itself was fine | repair the joint with high-temperature insulation (silicone / glass fibre sleeving), keep joints away from the hot pipe. Check: PT1000 = 1000 Ω at 0 °C, ~1078 Ω at 20 °C, ~1385 Ω at 100 °C, wiggle test |
| 0 °C alternating with -200 while the sensor is disconnected | controller's placeholder value after its alarm cancel attempt (`0225`) | nothing - the gateway ignores it |
| Set-point in Domoticz jumps back | controller did not accept the write | only `01F6` / `028E` work on the ST-480 |
| Several clicks = several slow steps | one command per poll (16 s) | queue merges writes to the same register |
| `nan` set-point in Domoticz | frontpage sent `NaN` because of a wrong `idx` | correct `idx`; gateway and frontpage ignore NaN |
| IP shown as `192.168 ??` | Domoticz created a number sensor from a value starting with digits | gateway sends `IP 192.168...`; delete and recreate the device |

## Domoticz

| Symptom | Fix |
|---|---|
| "Invalid location" when saving settings | fill in latitude / longitude first |
| Kiosk page empty, `curl` gives `401 Unauthorized` | add `127.0.0.*` to Trusted Networks |
| Frontpage "NaN" in the state cell | `idx_CPUusage` pointed to the state device - clear it |
| Broken diacritics in selector | `atob()` is not UTF-8 aware - patched with `b64utf8()` |
| Frontpage shows nothing / "not found" | Domoticz 2023.2+ removed `type=devices` - use the patch |

## MQTT

| Symptom | Fix |
|---|---|
| runit Mosquitto: `down, normally up`, log "Address already in use" | the package's init instance still runs: `sudo kill <pid>` |
| Listener also on `[::1]` | that instance ignores our config - same fix |

## System (Eee PC 901 / antiX)

| Symptom | Cause | Fix |
|---|---|---|
| Console keyboard: letters do nothing, J scrolls, Enter dead - also in the installer | **physically stuck Left Ctrl contact** (kernel sees Ctrl held from boot) | clean / reseat the key. Diagnose: `EVIOCGKEY` shows `[29]`, the key sends no events |
| Some letters type digits | embedded numeric keypad active (NumLock set by X / numlockx) | Fn+F11; `NUMLOCK=off` in `/etc/default/numlockx` |
| `grp:lalt_lshift_toggle` with a single layout | broken console keymap with ShiftL locks | remove group options, `setupcon --save-only` |
| Screen frozen after `sv down slimski` | X does not hand the console back | boot without the display manager instead |
| Two IP addresses on `wlan0` | `dhcpcd` and `connman` both doing DHCP | disable `dhcpcd` |
| `permission denied` on `/dev/ttyUSB0` | group | antiX/Debian: `dialout`; Arch/CachyOS: `uucp` |
| `apt` download fails on the antiX mirror | Greek mirror down | switch to `http://tux.rainside.sk/mxlinux/antix/` (keep `signed-by`) |
| `connmanctl connect` over SSH drops and never asks for a password | session gone before the prompt | provisioning file + `nohup` (see installation) |
| Ping spikes up to 120 ms | Wi-Fi power saving | `iw dev wlan0 set power_save off` |
| Telegram "BotFather" answers about "subscriptions" | fake bot | use `https://t.me/BotFather`, verified badge |
| Bot ignores the group | group ID missing / changed (groups upgraded to supergroups get a new `-100...` ID) | `grep Ignorujem /tmp/st480-bot.log` |

## Useful commands

```bash
tail -f /tmp/st480.log                                 # gateway
mosquitto_sub -h 127.0.0.1 -t 'st480/#' -v             # everything on MQTT
python3 ~/st480/cmd_st480.py -w 01f6 58                # raw write (allowed registers only)
sudo sv down st480-gw && python3 ~/st480/st480_capture.py   # raw bus capture
python3 ~/st480/st480_gw.py --replay capture.log       # decode a capture offline
```
