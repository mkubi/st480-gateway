#!/bin/sh
# Kiosk pre EeePC: X server bez desktopu + prehliadac surf na celu obrazovku
# s frontpage Domoticzu. Spustit: sudo sh kiosk-setup.sh
#
# - pouzivatel "kiosk" (bez sudo) sa automaticky prihlasi na tty1 a hned
#   spusti X; ked X skonci, prihlasi sa znova (ziadny shell na obrazovke)
# - na spravu pouzi SSH alebo konzolu tty2 (Ctrl+Alt+F2)
# - v noci 22:00-6:00 displej zhasne, lubovolny klaves ho rozsvieti
# - prehliadac sa kazdu noc o 4:30 restartuje (proti rastu pamate)
set -e

URL="http://127.0.0.1:8080/frontpage.html"
KUSER=kiosk

apt-get install -y --no-install-recommends \
    surf matchbox-window-manager unclutter xinit x11-xserver-utils xserver-xorg-legacy

# X server smie spustit aj bezny pouzivatel na konzole
cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=console
needs_root_rights=yes
EOF

id "$KUSER" >/dev/null 2>&1 || useradd -m -s /bin/sh "$KUSER"
usermod -aG video,input,audio,tty "$KUSER"

# po prihlaseni na tty1 hned spusti X, bez shellu
cat > /home/$KUSER/.profile << 'EOF'
if [ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ]; then
    exec startx > /tmp/kiosk-x.log 2>&1
fi
EOF

cat > /home/$KUSER/.xinitrc << EOF
#!/bin/sh
xset s off          # ziadny setric obrazovky
xset +dpms
xset dpms 0 0 0     # displej nezhasina sam, riadi to cron
unclutter -idle 3 -root &
matchbox-window-manager -use_titlebar no &
while true; do
    surf -F "$URL"
    sleep 5
done
EOF
chmod +x /home/$KUSER/.xinitrc
chown $KUSER:$KUSER /home/$KUSER/.profile /home/$KUSER/.xinitrc

crontab -u $KUSER - << 'EOF'
# displej v noci vypnuty (lubovolny klaves ho zapne)
0 22 * * * DISPLAY=:0 xset dpms force off
0 6  * * * DISPLAY=:0 xset dpms force on
# denny restart prehliadaca
30 4 * * * pkill -x surf
EOF

# automaticke prihlasenie na tty1 (povodny subor zostane ako run.bak)
RUN=/etc/sv/getty-tty1/run
[ -f $RUN.bak ] || cp $RUN $RUN.bak
sed -i "s#getty 38400 tty1#getty --autologin $KUSER --noclear 38400 tty1#" $RUN

echo
echo "=== $RUN ==="
cat $RUN
echo
echo "Hotovo. V riadku s getty musi byt '--autologin $KUSER'."
echo "Kiosk spustis:  sudo sv restart getty-tty1   (alebo reboot)"
echo "Spat na konzolu: sudo cp $RUN.bak $RUN && sudo sv restart getty-tty1"
