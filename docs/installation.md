# Installation / disaster recovery

Step-by-step rebuild of the whole system. Written for an **Asus Eee PC 901 with
antiX 26 (32-bit, runit)**, but works on any Debian-based system; with systemd only
the service files differ.

What you need to keep somewhere **outside** the machine (see [Backup](#backup)):

- this repository
- `~/st480/telegram.conf` (bot token and chat IDs - never in git)
- `~/dev-domoticz/domoticz.db` (Domoticz devices, history, settings)
- the complete original frontpage folder (`frontpage.html`, css, icons, js)

## 1. Operating system

Install antiX (the "full" edition is fine, then slim it down):

```bash
sudo apt clean
# firmware for hardware the Eee PC 901 does not have (~850 MB)
# KEEP firmware-mediatek: it contains rt2860.bin for the Ralink Wi-Fi!
sudo apt-mark manual firmware-mediatek
sudo apt purge firmware-qcom-soc firmware-netronome firmware-iwlwifi firmware-amd-graphics \
     firmware-marvell-prestera firmware-sof-signed firmware-atheros firefox-esr 'libreoffice*'
```

Never run `apt autoremove` blindly - always `apt autoremove -s` first.

Disable services a boiler server does not need (runit: delete the link, the service
stays in `/etc/sv` and can be re-enabled with `ln -s`):

```bash
for s in bluetooth cups saned rsync getty-ttyS0 dhcpcd; do
    sudo sv down $s; sudo rm /etc/runit/runsvdir/default/$s
done
```

`dhcpcd` must go because **connman** already does DHCP - both running gives the
Wi-Fi two IP addresses. Keep `ssh`, `udevd`, `dbus`, `connman`, `cron`, `acpid`,
`getty-tty1..6`.

Other settings:

```bash
# console blanking after 60 s: add consoleblank=60 to GRUB_CMDLINE_LINUX_DEFAULT
sudo nano /etc/default/grub && sudo update-grub
# Wi-Fi power saving off (stable latency) - also add to /etc/rc.local
sudo iw dev wlan0 set power_save off
# serial port access
sudo usermod -aG dialout $USER
```

Keyboard: `/etc/default/keyboard` should **not** contain group switching options
when only one layout is set:

```
XKBLAYOUT="us"
XKBOPTIONS="terminate:ctrl_alt_bksp"
```

then `sudo setupcon --save-only`. See [troubleshooting](troubleshooting.md).

## 2. Network

```bash
connmanctl scan wifi
connmanctl services
```

Connecting over SSH drops the session, so provision the network by file and switch
in the background:

```bash
sudo tee /var/lib/connman/wifi.config > /dev/null << 'EOF'
[service_home]
Type = wifi
Name = YOUR_SSID
Passphrase = YOUR_PASSWORD
EOF
sudo chmod 600 /var/lib/connman/wifi.config
nohup sh -c 'sleep 3; connmanctl connect wifi_..._managed_psk' > /tmp/wifi.log 2>&1 &
```

Give the machine a fixed address with a DHCP reservation in the router. Note that
Wi-Fi extenders often replace the client's MAC address.

## 3. Domoticz

Build from source following the
[Domoticz wiki](https://wiki.domoticz.com/Build_Domoticz_from_source) into
`~/dev-domoticz`. Do not use the init script from the wiki - `install.sh --domoticz`
creates a runit service (foreground, no SSL, log in `/tmp/domoticz.log`).

After the first start, in **Setup -> Settings**:

1. fill in **Location** (latitude/longitude) - without it no setting can be saved
   ("Invalid location")
2. **Security**: set a username/password, **Trusted Networks** `192.168.0.*;127.0.0.*`
   (127.0.0.* is needed by the kiosk)
3. enable **Accept new Hardware Devices**

## 4. Gateway, MQTT broker, bot

```bash
git clone https://github.com/<you>/st480-gateway.git && cd st480-gateway
sudo ./install.sh --bot --domoticz ~/dev-domoticz
```

The installer:

- installs `python3-serial python3-paho-mqtt mosquitto mosquitto-clients`
- copies the scripts to `~/st480/`
- configures Mosquitto (localhost only, no persistence - saves the old SSD) and
  replaces its init script by a runit service
- creates runit services `mosquitto`, `st480-gw`, optionally `st480-bot`, `domoticz`
- copies the dzVents alert script into Domoticz

Check:

```bash
sudo sv status mosquitto st480-gw
tail -f /tmp/st480.log
mosquitto_sub -h 127.0.0.1 -t 'st480/#' -v
```

In Domoticz **Setup -> Hardware** add **MQTT Auto Discovery Client Gateway with LAN
interface**: address `127.0.0.1`, port `1883`, auto discovery prefix `homeassistant`
(TLS field can stay as it is, it is only used with a CA file). All devices appear
in **Setup -> Devices** once the first data arrive. Hide the two "... Mode" devices
created next to the thermostats.

If the serial port name changes after reboots, use the stable path
`/dev/serial/by-id/...` and set `SERIAL_PORT` in `st480_gw.py`.

## 5. Telegram

1. Open **https://t.me/BotFather** (blue verified badge - there are fakes!),
   `/newbot`, keep the token secret.
2. Create a group with everybody who should get alerts and add the bot.
3. Write `/start@your_bot` in the group, then get the chat ID (negative number):

   ```bash
   TOKEN='123:ABC...'
   curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | grep -o '"chat":{"id":[0-9-]*'
   ```

4. Domoticz **Setup -> Settings -> Notifications -> Telegram**: API key = token,
   Chat ID = the group ID (one ID only; everybody in the group gets the message).
5. Bot: `nano ~/st480/telegram.conf` (TOKEN, CHATS), `sudo sv restart st480-bot`.
   If the bot ignores the group, `grep Ignorujem /tmp/st480-bot.log` shows the real ID.
6. Optional: BotFather `/setcommands` so the commands appear in the menu:

   ```
   stav - prehľad kotla
   uk - žiadaná ÚK, napr. /uk 58
   tuv - žiadaná TÚV, napr. /tuv 50
   rezim - režim: kurenie, priorita, paralelne, letny
   pomoc - zoznam príkazov
   ```

Alerts (`domoticz/kotol_notifikacie.lua`): alarm start/end, CH over 88 °C, no data
for 15 min, DHW below 40 °C / above 50 °C. Thresholds at the top of the script.
Simulate an alarm: `mosquitto_pub -h 127.0.0.1 -t st480/stav/state -m "ALARM: TEST"`.

## 6. Frontpage and kiosk

Copy the original frontpage into `~/dev-domoticz/www/` (page at
`http://<ip>:8080/frontpage.html`, scripts in `www/js/`), apply the patch and our
settings:

```bash
cd ~/dev-domoticz/www/js
patch frontpage.js < ~/st480-gateway/domoticz/frontpage/frontpage.js.patch
cp ~/st480-gateway/domoticz/frontpage/frontpage_settings.js .
```

The patch: new Domoticz API (`param=getdevices`), set-points via `setsetpoint`
(so the change goes to MQTT), UTF-8 selector labels, all selector levels shown,
confirm buttons (`fp_buttons`), read-only switches (`fp_readonly`).
**The `idx` numbers in `frontpage_settings.js` must match your Domoticz devices.**

Store jQuery locally so the kiosk works without internet:

```bash
cd ~/dev-domoticz/www/js
wget http://code.jquery.com/jquery-1.11.0.min.js
wget -O jquery-1.10.2.min.js https://ajax.googleapis.com/ajax/libs/jquery/1.10.2/jquery.min.js
# and point the <script src> lines in frontpage.html to js/...
```

Kiosk (X without desktop, `surf` full screen, user `kiosk` auto-login on tty1,
display off 22:00-6:00):

```bash
sudo sh ~/st480-gateway/system/kiosk-setup.sh
sudo sv restart getty-tty1
```

Admin console stays on Ctrl+Alt+F2 and SSH.

## Backup

```bash
# from another computer
scp kotol@<ip>:dev-domoticz/domoticz.db  backup/domoticz-$(date +%F).db
scp kotol@<ip>:st480/telegram.conf       backup/
scp -r kotol@<ip>:dev-domoticz/www/frontpage.html kotol@<ip>:dev-domoticz/www/js backup/frontpage/
```

Restoring Domoticz = build it, stop the service, put `domoticz.db` back, start it.
Devices keep their `idx`, so the frontpage settings stay valid.
