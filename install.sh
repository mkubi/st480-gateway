#!/bin/bash
# st480-gateway installer for antiX (runit) / Debian-based systems.
#   sudo ./install.sh                 gateway + MQTT broker
#   sudo ./install.sh --bot           + Telegram bot (needs bot/telegram.conf)
#   sudo ./install.sh --domoticz DIR  + runit service for Domoticz built in DIR
set -e
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
USER_NAME=${SUDO_USER:-kotol}
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
DIR=$HOME_DIR/st480
SRC=$(cd "$(dirname "$0")" && pwd)
SV=/etc/sv
RUNSVDIR=/etc/runit/runsvdir/default
BOT=0; DOMOTICZ=""
while [ $# -gt 0 ]; do
    case $1 in
        --bot) BOT=1 ;;
        --domoticz) DOMOTICZ=$2; shift ;;
        *) echo "Unknown option $1"; exit 1 ;;
    esac; shift
done

echo "== packages"
apt-get install -y python3-serial python3-paho-mqtt mosquitto mosquitto-clients
usermod -aG dialout "$USER_NAME"

echo "== files -> $DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" "$DIR"
install -o "$USER_NAME" -g "$USER_NAME" -m 755 "$SRC"/gateway/*.py "$SRC"/tools/st480_capture.py "$DIR"/
install -o "$USER_NAME" -g "$USER_NAME" -m 644 "$SRC"/tools/sample_capture.log "$DIR"/
install -m 644 "$SRC"/system/mosquitto/st480.conf /etc/mosquitto/conf.d/st480.conf

service() {   # $1 = name: install runit service from template
    install -d "$SV/$1"
    sed -e "s#@USER@#$USER_NAME#g" -e "s#@DIR@#$DIR#g" -e "s#@DOMOTICZ@#$DOMOTICZ#g" \
        "$SRC/system/runit/$1/run" > "$SV/$1/run"
    chmod +x "$SV/$1/run"
    [ -e "$RUNSVDIR/$1" ] || ln -s "$SV/$1" "$RUNSVDIR/$1"
    echo "   runit service $1 enabled"
}

echo "== services"
if [ -x /etc/init.d/mosquitto ]; then          # packaged init script would hold port 1883
    /etc/init.d/mosquitto stop || true
    pkill -x mosquitto || true
    update-rc.d mosquitto disable 2>/dev/null || true
fi
service mosquitto
service st480-gw

if [ $BOT = 1 ]; then
    install -o "$USER_NAME" -g "$USER_NAME" -m 755 "$SRC"/bot/st480_bot.py "$DIR"/
    if [ ! -f "$DIR/telegram.conf" ]; then
        install -o "$USER_NAME" -g "$USER_NAME" -m 600 "$SRC"/bot/telegram.conf.example "$DIR"/telegram.conf
        echo "   !!! edit $DIR/telegram.conf (TOKEN, CHATS), then: sudo sv restart st480-bot"
    fi
    service st480-bot
fi

if [ -n "$DOMOTICZ" ]; then
    [ -x "$DOMOTICZ/domoticz" ] || { echo "Domoticz binary not found in $DOMOTICZ"; exit 1; }
    service domoticz
    install -d "$DOMOTICZ/scripts/dzVents/scripts"
    install -o "$USER_NAME" -g "$USER_NAME" -m 644 "$SRC"/domoticz/kotol_notifikacie.lua \
        "$DOMOTICZ/scripts/dzVents/scripts/"
fi

echo
echo "Done. Check:  sudo sv status mosquitto st480-gw   and   tail -f /tmp/st480.log"
echo "Log out and back in so that '$USER_NAME' gets the dialout group."
