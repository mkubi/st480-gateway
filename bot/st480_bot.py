#!/usr/bin/env python3
"""
Telegram bot pre kotol ST-480.

Cita hodnoty z MQTT (posiela ich st480_gw.py) a odpoveda na prikazy
z povolenych chatov. Prikazy do kotla posiela cez MQTT rovnako ako Domoticz,
takze plati aj obmedzenie rozsahu a cakanie na vyzvu kotla v gatewayi.

Prikazy:
    /stav                 prehlad kotla
    /uk  [teplota]        ziadana UK - bez cisla len zobrazi
    /tuv [teplota]        ziadana TUV - bez cisla len zobrazi
    /rezim [nazov]        kurenie | priorita | paralelne | letny
    /pomoc                zoznam prikazov

Nastavenie v subore telegram.conf (vedla skriptu):
    TOKEN=123456789:AAH...
    CHATS=-987654321,123456789      povolene chaty (skupina, osoby)
"""

import json
import logging
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_BASE = "st480"
API = "https://api.telegram.org"      # pre testy sa da zmenit v telegram.conf
CONFIRM_AFTER = 40                    # s - po tomto case over, ci kotol prikaz prijal

REZIMY = {                            # skratka -> presny nazov v gatewayi
    "kurenie": "Kúrenie",
    "priorita": "Priorita TÚV",
    "paralelne": "Paralelné čerpadlá",
    "letny": "Letný",
}

log = logging.getLogger("st480-bot")


def plain(text):
    """Bez diakritiky a malymi pismenami (aby slo 'letny' aj 'letný')."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


def load_config():
    cfg = {}
    path = Path(__file__).with_name("telegram.conf")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip().upper()] = v.strip()
    chats = {int(c) for c in cfg.get("CHATS", "").replace(" ", "").split(",") if c}
    if not cfg.get("TOKEN") or not chats:
        sys.exit(f"V {path} chyba TOKEN alebo CHATS")
    return cfg["TOKEN"], chats, cfg.get("API", API)


class Telegram:
    def __init__(self, token, api):
        self.base = f"{api}/bot{token}/"

    def call(self, method, params=None, http_timeout=15):
        data = urllib.parse.urlencode(params or {}).encode()
        with urllib.request.urlopen(self.base + method, data, timeout=http_timeout) as r:
            res = json.loads(r.read())
        if not res.get("ok"):
            raise RuntimeError(res.get("description", res))
        return res["result"]

    def send(self, chat, text):
        try:
            self.call("sendMessage", {"chat_id": chat, "text": text})
        except Exception as ex:
            log.error("Odoslanie spravy zlyhalo: %s", ex)


class Kotol:
    """Posledne zname hodnoty z MQTT a odosielanie prikazov."""

    def __init__(self):
        self.state = {}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="st480-bot")
        self.client.on_connect = lambda c, *a: c.subscribe(f"{MQTT_BASE}/#")
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 60)
        self.client.connect_async(MQTT_HOST, MQTT_PORT)
        self.client.loop_start()

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) == 3 and parts[2] == "state":
            self.state[parts[1]] = msg.payload.decode("utf-8", "replace")
        elif len(parts) == 2 and parts[1] == "status":
            self.state["_status"] = msg.payload.decode()

    def get(self, key, unit=""):
        v = self.state.get(key)
        return f"{v}{unit}" if v not in (None, "") else "--"

    def set(self, key, value):
        self.client.publish(f"{MQTT_BASE}/{key}/set", str(value))


def prehlad(k):
    onoff = {"ON": "zap", "OFF": "vyp"}
    lines = [
        f"Kotol: {k.get('stav')}",
        f"ÚK: {k.get('uk_aktualna', ' °C')} (žiadaná {k.get('uk_setpoint', ' °C')})",
        f"TÚV: {k.get('tuv_aktualna', ' °C')} (žiadaná {k.get('tuv_setpoint', ' °C')})",
        f"Spaliny: {k.get('teplota_spalin', ' °C')}   Podávač: {k.get('teplota_podavaca', ' °C')}",
        f"Ventilátor: {k.get('ventilator_vykon', ' %')}   Palivo: {k.get('palivo_uroven', ' %')}",
        "Čerpadlá: ÚK {}, TÚV {}".format(onoff.get(k.state.get("cerpadlo_uk"), "--"),
                                         onoff.get(k.state.get("cerpadlo_tuv"), "--")),
        f"Režim: {k.get('rezim')}",
    ]
    if k.state.get("_status") == "offline":
        lines.insert(0, "POZOR: gateway je offline, hodnoty môžu byť staré!")
    return "\n".join(lines)


POMOC = """Príkazy:
/stav - prehľad kotla
/uk 58 - žiadaná ÚK (bez čísla len zobrazí)
/tuv 50 - žiadaná TÚV (bez čísla len zobrazí)
/rezim letny - kurenie | priorita | paralelne | letny
/pomoc - tento zoznam"""


def overit_neskor(tg, k, chat, key, cakana, nazov):
    """Po case pozri, ci kotol hlasi novu hodnotu, a daj vediet."""
    def check():
        teraz = k.state.get(key, "")
        if plain(teraz) == plain(cakana):
            tg.send(chat, f"Kotol potvrdil: {nazov} = {teraz}")
        else:
            tg.send(chat, f"Kotol zatiaľ hlási {nazov} = {teraz or '--'} (nie {cakana}).")
    threading.Timer(CONFIRM_AFTER, check).start()


def handle(tg, k, chat, text):
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return
    cmd = words[0][1:].split("@")[0].lower()     # /stav@kotol_bot -> stav
    arg = " ".join(words[1:])

    if cmd in ("stav", "teplota", "start"):
        tg.send(chat, prehlad(k))
    elif cmd in ("uk", "tuv"):
        key = f"{cmd}_setpoint"
        nazov = "žiadaná ÚK" if cmd == "uk" else "žiadaná TÚV"
        if not arg:
            tg.send(chat, f"{nazov.capitalize()}: {k.get(key, ' °C')}")
            return
        try:
            val = round(float(arg.replace(",", ".")))
        except ValueError:
            tg.send(chat, f"Nerozumiem číslu „{arg}“. Príklad: /{cmd} 55")
            return
        k.set(key, val)
        tg.send(chat, f"Posielam {nazov} {val} °C, kotol potvrdí do ~20 s.")
        overit_neskor(tg, k, chat, key, f"{val}", nazov)
    elif cmd == "rezim":
        if not arg:
            tg.send(chat, f"Režim: {k.get('rezim')}\nMožnosti: " + ", ".join(REZIMY))
            return
        rez = REZIMY.get(plain(arg).split()[0])
        if not rez:
            tg.send(chat, "Neznámy režim. Možnosti: " + ", ".join(REZIMY))
            return
        k.set("rezim", rez)
        tg.send(chat, f"Prepínam režim na {rez}, kotol potvrdí do ~20 s.")
        overit_neskor(tg, k, chat, "rezim", rez, "režim")
    elif cmd in ("pomoc", "help"):
        tg.send(chat, POMOC)
    else:
        tg.send(chat, "Neznámy príkaz.\n" + POMOC)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    token, chats, api = load_config()
    tg = Telegram(token, api)
    k = Kotol()
    log.info("Bot spusteny, povolene chaty: %s", sorted(chats))
    offset = None
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            updates = tg.call("getUpdates", params, http_timeout=60)
        except Exception as ex:
            log.warning("Telegram nedostupny: %s - skusim znova o 10 s", ex)
            time.sleep(10)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message")
            if not msg or "text" not in msg:
                continue
            chat = msg["chat"]["id"]
            if chat not in chats:
                log.warning("Ignorujem spravu z nepovoleneho chatu %s", chat)
                continue
            log.info("Prikaz z %s: %s", chat, msg["text"])
            try:
                handle(tg, k, chat, msg["text"])
            except Exception as ex:
                log.error("Chyba pri spracovani: %s", ex)
                tg.send(chat, "Pri spracovaní príkazu nastala chyba.")


if __name__ == "__main__":
    main()
