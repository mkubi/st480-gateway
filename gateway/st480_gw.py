#!/usr/bin/env python3
"""
ST480 gateway - cita data z kotla (regulator TECH ST-480) cez seriovu linku
a publikuje ich do MQTT s automatickym objavovanim (Home Assistant MQTT
Discovery). Domoticz, Home Assistant aj ine systemy si zariadenia vytvoria
samy - ziadne idx.

Format ramca (overene na realnych datach):

    02 26 AA AA | RR RR VV VV | RR RR VV VV | ... | 02 18 CC CC

    AA AA        CIELOVA adresa: ff f4 = ethernet modul (= my),
                 ff fa = izbovy regulator, ff f8 = GSM modul,
                 00 00 = vsetky zariadenia (takto posielame prikazy my)
    RR RR VV VV  register a hodnota (big-endian)
    02 18 CC CC  koncovy "register", hodnota = CRC16/MCRF4XX
                 z bajtov pred nim (big-endian)

MQTT temy:
    st480/status                 online / offline
    st480/<kluc>/state           hodnota
    st480/<kluc>/set             prikaz (rezim, rozkurenie, vyhasinanie)
    homeassistant/.../config     popis zariadeni pre automaticke objavovanie

Pouzitie:
    st480_gw_mqtt.py                         bezna prevadzka
    st480_gw_mqtt.py --log -                 log na konzolu
    st480_gw_mqtt.py --dry-run --log -       bez MQTT, len loguje co by poslal
    st480_gw_mqtt.py --replay st480_raw.log  prehra zaznam
"""

import argparse
import json
import math
import logging
import logging.handlers
import queue
import signal
import socket
import threading
import time

import serial

try:
    import paho.mqtt.client as mqtt
except ImportError:          # --replay a --dry-run funguju aj bez paho
    mqtt = None

# ---------------------------------------------------------------------------
# Konfiguracia
# ---------------------------------------------------------------------------
SERIAL_PORT = "/dev/ttyUSB0"       # odporucam /dev/serial/by-id/...
BAUDRATE = 9600
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_BASE = "st480"                # zaciatok vsetkych tem
DISCOVERY_PREFIX = "homeassistant" # Domoticz aj Home Assistant pouzivaju tento
LISTEN_HOST = "127.0.0.1"          # TCP pre cmd_st480.py (spatna kompatibilita)
LISTEN_PORT = 50080

# Kotol posiela kazdych ~16 s prazdny ramec 0226 fff4 0218 = vyzva pre
# ethernet modul. Prikaz posleme hned po nej, ako by to robil original.
SEND_AFTER_POLL = True
POLL_REPLY_DELAY = 0.05  # s po vyzve
POLL_FALLBACK = 20.0     # s - ak vyzva nepride, posli po obycajnej pauze
IDLE_GAP = 0.3           # s ticha na zbernici (pre fallback)
PARTIAL_TIMEOUT = 1.0    # s - neukonceny ramec po tomto case zahodime
REFRESH_INTERVAL = 300   # s - aj nezmenene hodnoty posleme raz za 5 min

# Kam sa zapisuju ziadane teploty z Domoticzu. 157E/1616 na kotli nefunguje,
# kandidati zo slovnika: 01F6 / 028E ("od regulatora"), 02FC ("zmena UK").
SETPOINT_REG_UK = 0x01F6
SETPOINT_REG_TUV = 0x028E

ADDR_ETH = 0xFFF4        # ramce pre ethernet modul - tie citame
ADDR_ALL = 0x0000        # ramce pre vsetkych - tak posielame prikazy
SENSOR_MISSING = 0xF830   # -200.0 C = regulator hlasi chybajuci / vadny snimac
ADDR_BROADCAST = 0xFFFF   # oznamenie pre vsetky moduly (napr. 0225 po zruseni alarmu)
ADDR_NAMES = {0xFFFA: "izbovy regulator", 0xFFF8: "GSM modul"}

DEVICE = {
    "identifiers": ["st480"],
    "name": "Kotol ST-480",
    "manufacturer": "TECH",
    "model": "ST-480",
}

# Informacie o samotnom EeePC (IP adresa, Wi-Fi) - samostatne zariadenie
HOST_DEVICE = {"identifiers": ["st480_host"], "name": "EeePC", "model": "Eee PC 901"}
HOST_ENTITIES = {
    "host_ip": dict(name="IP adresa"),
    "host_wifi": dict(name="Wi-Fi signál", unit="%"),
}
HOST_INTERVAL = 60       # s

DAYS = ["nedela", "pondelok", "utorok", "streda", "stvrtok", "piatok", "sobota"]

# Stavy regulatora (157c). Zaklad je tabulka 01F4 zo slovnika dzien.net,
# prepisana hodnotami, ktore slovnik uvadza priamo pre 157C.
STATES = {
    0x0000: "ZIADNY", 0x0001: "TEST", 0x0002: "PREVADZKA", 0x0003: "PRESTAVKA",
    0x0004: "UDRZIAVANIE - PREVADZKA", 0x0005: "UDRZIAVANIE - PRESTAVKA",
    0x0006: "RUCNA PREVADZKA", 0x0007: "ONESKORENIE PODAVACA",
    0x0008: "ONESKORENIE VENTILATORA", 0x0009: "ALARM: TEPLOTA NESTUPA",
    0x000A: "ALARM: CHYBA SNIMACA PODAVACA", 0x000B: "ALARM: PRILIS VYSOKA TEPLOTA UK",
    0x000C: "ALARM: PRILIS VYSOKA TEPLOTA TUV", 0x000D: "ALARM: CHYBA SNIMACA TUV",
    0x000E: "ALARM: CHYBA SNIMACA UK", 0x000F: "ALARM: PRILIS VYSOKA TEPLOTA PODAVACA",
    0x0010: "ROST", 0x0011: "ALARM: NEUSPESNE ROZKURENIE",
    0x0012: "ALARM: CHYBA SNIMACA SPALIN", 0x0013: "ALARM: CHYBA SNIMACA SPIATOCKY",
    0x0014: "ALARM: TEPELNA OCHRANA MOTORA", 0x0015: "ALARM: ZIADNA KOMUNIKACIA",
    0x0016: "ALARM: CHYBA PALIVO", 0x0017: "ALARM: KONTAKTRON",
    0x0018: "ALARM: TEPELNA POISTKA", 0x0019: "NEUSPESNA PRECHODOVA FAZA",
    0x001A: "UHASENY", 0x001B: "ALARM: CHYBA SNIMACA PODLAHY",
    0x001C: "ALARM: SKRAT VENTILATORA", 0x001D: "ALARM: CHYBA SNIMACA VENTILU",
    0x001E: "RUCNA PREVADZKA", 0x001F: "ROZKURENIE", 0x0020: "PLNENIE PALIVA",
    0x0021: "VYHASNUTY", 0x0022: "VYHASINANIE", 0x0023: "ROZKURENIE",
    0x0024: "ROZKURENIE", 0x0025: "CISTENIE ROSTU", 0x0026: "VYPINANIE DOSYPAVANIA",
    0x0027: "ALARM: CHYBA SNIMACA PODLAHY", 0x0028: "DRUHA FAZA VYPINANIA",
    0x0029: "ALARM: CHYBA SNIMACA VENTILU", 0x002A: "STANDBY",
    0x002B: "PRECHODOVA FAZA", 0x002C: "PRECHODOVA FAZA",
    0x002D: "ONESKORENIE ROZKURENIA", 0x002E: "MODULACIA - PREVADZKA",
    0x002F: "MODULACIA - PRESTAVKA", 0x0030: "TEST PODAVACA - CAKANIE",
    0x0031: "ROZKURENIE", 0x0032: "TEST VENTILATORA", 0x0033: "TEST CERPADLA UK",
    0x0034: "TEST CERPADLA TUV", 0x0035: "TEST ZAPISU - CAKANIE", 0x0036: "ZAPIS TESTU",
    0x0037: "NASTAVENIE SERVISNEHO KODU", 0x0038: "DOSYPAVANIE - CAKANIE",
    0x0039: "VYPINANIE DOSYPU", 0x003A: "VYHASINANIE", 0x003B: "PREFUK",
    0x003C: "PREFUK", 0x003D: "TEST OHNA", 0x003F: "MAKKY START", 0x0040: "MAKKY START",
    0x0041: "PREVADZKA - CAKANIE", 0x0042: "KALIBRACIA VENTILATORA",
    0x0043: "TEST SNIMACA SPALIN", 0x0045: "UDRZIAVANIE T2 - PRESTAVKA",
    0x0046: "UDRZIAVANIE T2 - PREVADZKA", 0x0047: "ONESKORENIE PODAVACA",
    0x0048: "ONESKORENIE VENTILATORA", 0x0049: "PREVADZKA BEZ PID",
    0x004A: "UDRZIAVANIE BEZ PID - PRESTAVKA", 0x004B: "UDRZIAVANIE BEZ PID - PREVADZKA",
    0x004C: "PREDALARM PODAVACA", 0x004D: "ALARM: ZLY TYP VENTILATORA",
    0x004E: "ROZKURENIE", 0x004F: "ROZKURENIE", 0x0050: "UDRZIAVANIE",
    0x0051: "SPRAVA", 0x0052: "UTLM", 0x0053: "RUCNE KURENIE", 0x0054: "RUCNE KURENIE",
    0x0055: "RUCNE KURENIE", 0x0056: "TEST OHNA", 0x0057: "TEST OHNA",
    0x0058: "TEST OHNA", 0x0059: "POSUV PODAVACA VPRED", 0x005A: "POSUV PODAVACA VZAD",
    0x005B: "UTLM - VENTILATOR BEZI", 0x005C: "UTLM - VENTILATOR STOJI",
    0x005D: "MAKKY START", 0x005E: "PRECHODOVA FAZA", 0x005F: "UDRZIAVANIE VYPNUTE",
    0x0060: "POZASTAVENIE", 0x0061: "MERANIE SPOTREBY PALIVA", 0x0082: "UTLM",
    0x00C8: "ALARM: CHYBA SNIMACA RIADENIA VENTILATORA", 0x00C9: "ALARM: HALLOV SNIMAC ROSTU",
    0x00CA: "ALARM: CHYBA SNIMACA PRIDAVNEHO CERPADLA",
    0x00CB: "ALARM: PRILIS VYSOKA TEPLOTA RIADENIA VENTILATORA",
    0x00CD: "ALARM: ZIADNA KOMUNIKACIA S VENTILOM", 0x00CE: "ALARM: CHYBA VONKAJSIEHO SNIMACA",
    0x00CF: "ALARM: PRILIS VYSOKA TEPLOTA PODLAHY", 0x00D0: "ALARM: CHYBA 2. SNIMACA AKUMULACKY",
    0x00D1: "ALARM: CHYBA IZBOVEHO SNIMACA", 0x00D2: "ALARM: CHYBA SNIMACA AKUMULACKY",
    0x00D3: "ALARM: HALLOV SNIMAC PODAVACA", 0x00D4: "ALARM: CHYBA SNIMACA SOLARU 1",
    0x00D5: "ALARM: CHYBA SNIMACA SOLARU 2", 0x00D6: "ALARM: CHYBA SNIMACA SOLARU 3",
    0x00D7: "ALARM: CHYBA SNIMACA SOLARU 4",
}

REZIM_LABELS = {0: "Kúrenie", 1: "Priorita TÚV", 2: "Paralelné čerpadlá", 3: "Letný"}

# Registre z kotla -> MQTT entity.
#   comp:  sensor / binary_sensor / select (typ entity pre objavovanie)
#   key:   kratky nazov, pouzity v temach a unique_id - NEMENIT, inak vznikne
#          v Domoticzi nove zariadenie
#   scale: delitel, signed: hodnota so znamienkom, valid: platny rozsah
ENTITIES = {
    0x157d: dict(key="uk_aktualna", name="ÚK aktuálna", comp="sensor",
                 unit="°C", dc="temperature", scale=10, signed=True),
    # ziadane teploty: citame 157e/1616, zapisujeme do registra v write_reg
    # (podla povodnej verzie gatewaya ten isty register - OVERIT PRI KOTLI,
    # alternativa zo slovnika: 0x01F6 pre UK, 0x028E pre TUV)
    0x157e: dict(key="uk_setpoint", name="ÚK žiadaná", comp="climate",
                 write_reg=SETPOINT_REG_UK, limits_reg=0x169e, default_limits=(30, 80)),
    0x166e: dict(key="tuv_aktualna", name="TÚV aktuálna", comp="sensor",
                 unit="°C", dc="temperature", scale=10, signed=True),
    0x1616: dict(key="tuv_setpoint", name="TÚV žiadaná", comp="climate",
                 write_reg=SETPOINT_REG_TUV, limits_reg=0x169f, default_limits=(40, 75)),
    0x1681: dict(key="teplota_vonku", name="Teplota vonku", comp="sensor",
                 unit="°C", dc="temperature", scale=10, signed=True,
                 valid=(-50, 60)),
    0x15b7: dict(key="teplota_spalin", name="Teplota spalín", comp="sensor",
                 unit="°C", dc="temperature", scale=10, signed=True),
    0x16f8: dict(key="teplota_podavaca", name="Teplota podávača", comp="sensor",
                 unit="°C", dc="temperature", scale=10, signed=True),
    0x159b: dict(key="ventilator_vykon", name="Výkon ventilátora", comp="sensor",
                 unit="%"),
    # slovnik: percento paliva; delenie 512 je z povodneho kodu - overit
    0x16f1: dict(key="palivo_uroven", name="Úroveň paliva", comp="sensor",
                 unit="%", scale=512),
    # slovnik: orientacny cas zasoby paliva v hodinach
    0x16f2: dict(key="palivo_zasoba", name="Zásoba paliva", comp="sensor",
                 unit="h"),
    0x157c: dict(key="stav", name="Stav kotla", comp="sensor", texts=STATES),
    0x1589: dict(key="cerpadlo_uk", name="Čerpadlo ÚK", comp="binary_sensor"),
    0x158b: dict(key="cerpadlo_tuv", name="Čerpadlo TÚV", comp="binary_sensor"),
    0x1587: dict(key="podavac", name="Podávač", comp="binary_sensor"),
    0x1588: dict(key="ventilator", name="Ventilátor", comp="binary_sensor"),
    # rezim: citame 15cd, zapisujeme do 0245
    0x15cd: dict(key="rezim", name="Režim", comp="select",
                 labels=REZIM_LABELS, write_reg=0x0245),
}

# Tlacidla bez registra na citanie: MQTT prikaz -> zapis (register, hodnota).
# Rozkurenie (0209) a vyhasinanie (020A) na ST-480 nefunguju, preto prazdne.
BUTTONS = {}


def _minmax(v):
    return f"min {v & 0xFF} / max {v >> 8} C"


def _clock(v):
    return f"{v >> 8:02d}:{v & 0xFF:02d}"


def _day(v):
    return DAYS[v] if v < len(DAYS) else f"? ({v})"


CONTROLLER_TYPES = {0x0015: "ST-480", 0x0016: "ST-730zPID/ST-755zPID/ST-500",
                    0x0013: "ST-450zPID", 0x0007: "AG LUX / ST-755"}

# Registre, ktore neposielame, ale pri zmene ich zalogujeme.
INFO_REGISTERS = {
    0x169e: ("Limity UK", _minmax),
    0x169f: ("Limity TUV", _minmax),
    0x1620: ("Hodiny regulatora", _clock),
    0x1621: ("Den regulatora", _day),
    0x15a7: ("Typ regulatora", lambda v: CONTROLLER_TYPES.get(v, f"0x{v:04x}")),
    0x16ff: ("Typ regulatora 2", lambda v: "ST-480" if v == 6 else f"0x{v:04x}"),
    0x01f6: ("Nastavena UK od izb. regulatora", str),
    0x028e: ("Nastavena TUV od izb. regulatora", str),
    0x0298: ("Hodiny izb. regulatora", _clock),
    0x0299: ("Den izb. regulatora", _day),
    0x0245: ("Rezim cerpadiel (prikazovy reg.)", str),
    0x16f9: ("parameter pre predch. hodnotu", None),
    0x16c2: ("adresa ventilu", None),
    0x1610: ("Standby", lambda v: {0: "vypnuty", 1: "ZAPNUTY"}.get(v, f"0x{v:04x}")),
}
QUIET_REGISTERS = {0x1620, 0x0298}

# Limity ziadanych teplot, ktore kotol hlasi (169e/169f): reg -> (min, max)
LIMITS = {}

# Stare popisy zariadeni z predchadzajucej verzie - pri starte ich zmazeme
OBSOLETE_DISCOVERY = [
    "homeassistant/sensor/st480/uk_nastavena/config",
    "homeassistant/sensor/st480/tuv_nastavena/config",
    "homeassistant/switch/st480/rozkurenie/config",
    "homeassistant/switch/st480/vyhasinanie/config",
]

# Co smie klient cez TCP (cmd_st480.py) zapisat do kotla - len overene registre.
# Otestovane a NEFUNGUJUCE na ST-480: 157E/1616 (ziadane teploty), 02FC,
# 0209 (rozkurenie), 020A (vyhasinanie), 01FE-0203 (rucny rezim, podavac,
# ventilator, cerpadla) a 1587/1588/1589/158B (stavove registre zariadeni).
ALLOWED_WRITES = {
    0x0245: {0, 1, 2, 3},        # rezim cerpadiel
    0x01F6: set(range(30, 81)),  # ziadana UK
    0x028E: set(range(40, 76)),  # ziadana TUV
}

log = logging.getLogger("st480")

HDR = b"\x02\x26"
END = b"\x02\x18"
MAX_REGS = 64


# ---------------------------------------------------------------------------
# Protokol
# ---------------------------------------------------------------------------
def crc16_mcrf4xx(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def s16(v: int) -> int:
    """16-bitova hodnota so znamienkom (dvojkovy doplnok)."""
    return v - 0x10000 if v & 0x8000 else v


class FrameParser:
    """Sklada ramce z prudu bajtov, bez ohladu na to, ako su rozsekane."""

    def __init__(self):
        self.buf = bytearray()

    def reset(self):
        self.buf.clear()

    def pending(self) -> bool:
        return len(self.buf) > 0

    def feed(self, data: bytes):
        """Prida bajty, vrati zoznam kompletnych ramcov (addr, [(reg, val)])."""
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(HDR)
            if start < 0:
                # posledny bajt si nechame, moze to byt zaciatok hlavicky
                keep = 1 if self.buf[-1:] == b"\x02" else 0
                junk = len(self.buf) - keep
                if junk > 0:
                    log.debug("Bajty mimo ramca: %s", self.buf[:junk].hex())
                    del self.buf[:junk]
                break
            if start > 0:
                log.debug("Bajty mimo ramca: %s", self.buf[:start].hex())
                del self.buf[:start]

            end = self._find_end()
            if end is None:
                break                      # ramec este nie je cely
            if end < 0:
                log.warning("Ramec bez konca, hladam dalsiu hlavicku")
                del self.buf[:2]
                continue

            body = bytes(self.buf[:end])
            crc_rx = int.from_bytes(self.buf[end + 2:end + 4], "big")
            crc_calc = crc16_mcrf4xx(body)
            if crc_rx != crc_calc:
                log.warning("Zle CRC (prijate %04x, vypocitane %04x): %s",
                            crc_rx, crc_calc, self.buf[:end + 4].hex())
                del self.buf[:2]           # resync od dalsej hlavicky
                continue

            addr = int.from_bytes(body[2:4], "big")
            regs = [(int.from_bytes(body[i:i + 2], "big"),
                     int.from_bytes(body[i + 2:i + 4], "big"))
                    for i in range(4, end, 4)]
            frames.append((addr, regs))
            del self.buf[:end + 4]
        return frames

    def _find_end(self):
        """Pozicia koncoveho 02 18, None = treba viac dat, -1 = nezmysel."""
        pos = 4
        while pos + 4 <= len(self.buf):
            if self.buf[pos:pos + 2] == END:
                return pos
            pos += 4
            if pos > 4 + 4 * MAX_REGS:
                return -1
        return None



def build_frame(body: bytes) -> bytes:
    return body + END + crc16_mcrf4xx(body).to_bytes(2, "big")


def write_frame(reg: int, val: int) -> bytes:
    body = HDR + ADDR_ALL.to_bytes(2, "big") + reg.to_bytes(2, "big") + val.to_bytes(2, "big")
    return build_frame(body)


# ---------------------------------------------------------------------------
# Dekodovanie hodnot
# ---------------------------------------------------------------------------
def decode(e: dict, raw: int):
    """Surova hodnota -> MQTT payload (text), None = neplatna."""
    comp = e["comp"]
    if comp == "binary_sensor":
        return "ON" if raw == 1 else "OFF"
    if comp == "select":
        return e["labels"].get(raw)
    if "texts" in e:
        return e["texts"].get(raw, f"NEZNAMY 0x{raw:04x}")
    v = s16(raw) if e.get("signed") else raw
    scale = e.get("scale", 1)
    if scale != 1:
        v = round(v / scale, 1)
    lo, hi = e.get("valid", (None, None))
    if lo is not None and not lo < v < hi:
        return None
    if e.get("dc") == "temperature" and v <= -100:
        return None          # -200.0 (SENSOR_MISSING) = regulator hlasi odpojeny snimac
    return str(v)


class Decoder:
    """Dekoduje ramce kotla, loguje len zmeny (nie kazdy ramec)."""

    def __init__(self):
        self.last_raw = {}
        self.seen_unknown = set()

    def process(self, regs):
        updates = {}
        for reg, raw in regs:
            e = ENTITIES.get(reg)
            if e is None:
                self._other(reg, raw)
                continue
            prev = self.last_raw.get(reg)
            if raw == 0 and prev == SENSOR_MISSING and e.get("dc") == "temperature":
                # Regulator pri pokuse o zrusenie alarmu (0225) hodnotu odpojeneho
                # snimaca na chvilu vynuluje - to nie je meranie, ignorujeme
                log.debug("%s: 0 po chybajucom snimaci (zrusenie alarmu), ignorujem", e["name"])
                continue
            changed = prev != raw
            self.last_raw[reg] = raw
            value = decode(e, raw)
            if value is None:
                if changed:
                    log.warning("%s: neplatna hodnota 0x%04x (odpojeny snimac?)",
                                e["name"], raw)
                continue
            if changed:
                log.info("%s: %s", e["name"], value)
            updates[e["key"]] = value
        return updates

    def _other(self, reg, raw):
        info = INFO_REGISTERS.get(reg)
        if info:
            name, fmt = info
            if reg in (0x169e, 0x169f):
                LIMITS[reg] = (raw & 0xFF, raw >> 8)
            if fmt and self.last_raw.get(reg) != raw:
                quiet = reg in QUIET_REGISTERS and reg in self.last_raw
                log.log(logging.DEBUG if quiet else logging.INFO, "%s: %s", name, fmt(raw))
            else:
                log.debug("0x%04x (%s) = 0x%04x", reg, name, raw)
            self.last_raw[reg] = raw
        elif reg not in self.seen_unknown:
            self.seen_unknown.add(reg)
            log.info("Neznamy register 0x%04x = 0x%04x", reg, raw)
        else:
            log.debug("Neznamy register 0x%04x = 0x%04x", reg, raw)


def handle_frame(frame, decoder: Decoder):
    addr, regs = frame
    if addr == ADDR_ETH:
        return decoder.process(regs)
    text = " ".join(f"{r:04x}={v:04x}" for r, v in regs)
    if addr == ADDR_BROADCAST:
        for reg, val in regs:
            if reg == 0x0225:
                log.info("Regulator oznamil zrusenie alarmu (0225=%04x)", val)
            else:
                log.info("Oznamenie pre vsetky moduly: %04x=%04x", reg, val)
        return {}
    if addr == ADDR_ALL:
        log.debug("Ramec pre vsetkych (echo nasho prikazu?): %s", text)
    elif addr in ADDR_NAMES:
        log.debug("Ramec pre %s: %s", ADDR_NAMES[addr], text)
    else:
        log.info("Ramec pre neznamu adresu 0x%04x: %s", addr, text)
    return {}


def is_poll(frame) -> bool:
    """Prazdny ramec pre ethernet modul = vyzva, mozeme vysielat."""
    return frame[0] == ADDR_ETH and not frame[1]


# ---------------------------------------------------------------------------
# Fronta prikazov do kotla
# ---------------------------------------------------------------------------
class CommandQueue:
    """Prikazy cakajuce na vyzvu kotla. Novsi zapis do toho isteho registra
    nahradi starsi, este neodoslany (napr. 5 klikov na sipku = 1 prikaz)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}          # kluc -> (ramec, cas zaradenia), v poradi

    @staticmethod
    def _key(frame):
        return frame[4:6] if len(frame) == 12 else frame   # 1 register = kluc

    def put(self, frame: bytes):
        key = self._key(frame)
        with self.lock:
            old = self.items.get(key)
            if old:
                log.info("Nahradzujem cakajuci prikaz %s -> %s", old[0].hex(), frame.hex())
                self.items[key] = (frame, old[1])    # poradie aj cas ostavaju
            else:
                self.items[key] = (frame, time.monotonic())

    def oldest_since(self):
        with self.lock:
            return next(iter(self.items.values()))[1] if self.items else None

    def pop(self):
        with self.lock:
            if not self.items:
                return None
            return self.items.pop(next(iter(self.items)))[0]


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------
def topic(key, kind):
    return f"{MQTT_BASE}/{key}/{kind}"


def discovery_messages():
    """(tema, payload) pre vsetky entity - popis pre automaticke objavovanie."""
    avail = f"{MQTT_BASE}/status"
    msgs = []
    for e in ENTITIES.values():
        key, comp = e["key"], e["comp"]
        cfg = {"name": e["name"], "unique_id": f"st480_{key}",
               "object_id": f"st480_{key}", "state_topic": topic(key, "state"),
               "availability_topic": avail, "device": DEVICE}
        if "unit" in e:
            cfg["unit_of_measurement"] = e["unit"]
        if "dc" in e:
            cfg["device_class"] = e["dc"]
            cfg["state_class"] = "measurement"
        if comp == "select":
            cfg["options"] = list(e["labels"].values())
            cfg["command_topic"] = topic(key, "set")
        if comp == "climate":
            del cfg["state_topic"]
            lo, hi = LIMITS.get(e["limits_reg"], e["default_limits"])
            cfg.update({"temperature_state_topic": topic(key, "state"),
                        "temperature_command_topic": topic(key, "set"),
                        "min_temp": lo, "max_temp": hi, "temp_step": 1,
                        "temperature_unit": "C", "modes": ["heat"]})
        msgs.append((f"{DISCOVERY_PREFIX}/{comp}/st480/{key}/config", cfg))
    for key, h in HOST_ENTITIES.items():
        cfg = {"name": h["name"], "unique_id": f"st480_{key}",
               "object_id": f"st480_{key}", "state_topic": topic(key, "state"),
               "availability_topic": avail, "device": HOST_DEVICE}
        if "unit" in h:
            cfg["unit_of_measurement"] = h["unit"]
        msgs.append((f"{DISCOVERY_PREFIX}/sensor/st480/{key}/config", cfg))
    for key, b in BUTTONS.items():
        cfg = {"name": b["name"], "unique_id": f"st480_{key}",
               "object_id": f"st480_{key}", "state_topic": topic(key, "state"),
               "command_topic": topic(key, "set"),
               "availability_topic": avail, "device": DEVICE}
        msgs.append((f"{DISCOVERY_PREFIX}/switch/st480/{key}/config", cfg))
    return msgs


def command_from_mqtt(key: str, payload: str):
    """MQTT prikaz -> (register, hodnota) alebo None."""
    if key in BUTTONS:
        return BUTTONS[key]["write"] if payload.upper() == "ON" else None
    for e in ENTITIES.values():
        if e["key"] == key and e["comp"] == "climate":
            try:
                f = float(payload.replace(",", "."))
            except ValueError:
                return None
            if not math.isfinite(f):         # "nan", "inf" z Domoticzu
                return None
            want = round(f)
            lo, hi = LIMITS.get(e["limits_reg"], e["default_limits"])
            val = min(max(want, lo), hi)
            if val != want:
                log.warning("%s: %s mimo rozsahu %d-%d, pouzijem %d",
                            e["name"], payload, lo, hi, val)
            return e["write_reg"], val
        if e["key"] == key and e["comp"] == "select":
            for val, label in e["labels"].items():
                if label == payload:
                    return e["write_reg"], val
    return None


class MqttLink:
    """Publikuje hodnoty do MQTT, prijima prikazy. Pri dry_run len loguje."""

    def __init__(self, cmd_q: queue.Queue, dry_run=False):
        self.cmd_q = cmd_q
        self.dry_run = dry_run
        self.lock = threading.Lock()
        self.state = {}          # kluc -> (payload, cas odoslania)
        self.connected = False
        self.client = None
        if dry_run:
            return
        if mqtt is None:
            raise SystemExit("Chyba balik paho-mqtt: sudo apt install python3-paho-mqtt")
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="st480-gw")
        self.client.will_set(f"{MQTT_BASE}/status", "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(1, 60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        if self.client:
            self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
        else:
            for t, cfg in discovery_messages():
                log.debug("DRY-RUN discovery %s", t)

    def stop(self):
        if self.client:
            self.client.publish(f"{MQTT_BASE}/status", "offline", qos=1, retain=True)
            self.connected = False
            self.client.disconnect()
            self.client.loop_stop()

    def _publish(self, t, payload, retain=True):
        if self.dry_run:
            log.info("DRY-RUN %s = %s", t, payload)
        elif self.connected:
            self.client.publish(t, payload, qos=0, retain=retain)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.warning("MQTT pripojenie odmietnute: %s", reason_code)
            return
        log.info("MQTT pripojeny k %s:%d", MQTT_HOST, MQTT_PORT)
        self.connected = True
        for t in OBSOLETE_DISCOVERY:
            client.publish(t, "", qos=1, retain=True)
        for t, cfg in discovery_messages():
            client.publish(t, json.dumps(cfg, ensure_ascii=False), qos=1, retain=True)
        client.publish(f"{MQTT_BASE}/status", "online", qos=1, retain=True)
        client.subscribe(f"{MQTT_BASE}/+/set")
        for key in BUTTONS:
            client.publish(topic(key, "state"), "OFF", retain=True)
        with self.lock:          # po (re)konekte posli posledne zname hodnoty
            for key, (payload, _) in self.state.items():
                client.publish(topic(key, "state"), payload, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if self.connected:
            log.warning("MQTT odpojeny (%s), skusam znova...", reason_code)
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            key = msg.topic.split("/")[1]
            payload = msg.payload.decode("utf-8", "replace").strip()
        except (IndexError, UnicodeError):
            return
        try:
            cmd = command_from_mqtt(key, payload)
        except Exception as ex:
            log.error("Chyba pri spracovani prikazu %s = %s: %s", msg.topic, payload, ex)
            return
        if cmd is None:
            log.warning("Neznamy MQTT prikaz: %s = %s", msg.topic, payload)
            return
        reg, val = cmd
        log.info("MQTT prikaz %s = %s -> zapis %04x=%04x", key, payload, reg, val)
        self.cmd_q.put(write_frame(reg, val))
        if key in BUTTONS:        # tlacidlo sa hned vrati do OFF
            client.publish(topic(key, "state"), "OFF", retain=True)

    def sent(self, frame: bytes):
        """Prikaz odisiel do kotla: zabudni poslednu hodnotu, aby sa pri
        najblizsom ramci poslal skutocny stav (ukaze, ci kotol prikaz prijal)."""
        if len(frame) != 12:
            return
        reg = int.from_bytes(frame[4:6], "big")
        for e in ENTITIES.values():
            if e.get("write_reg") == reg:
                with self.lock:
                    self.state.pop(e["key"], None)

    def update(self, updates: dict):
        """Posle zmenene hodnoty (a raz za REFRESH_INTERVAL aj nezmenene)."""
        now = time.monotonic()
        with self.lock:
            for key, payload in updates.items():
                prev = self.state.get(key)
                if prev and prev[0] == payload and now - prev[1] < REFRESH_INTERVAL:
                    continue
                self.state[key] = (payload, now)
                self._publish(topic(key, "state"), payload)


# ---------------------------------------------------------------------------
# Informacie o EeePC
# ---------------------------------------------------------------------------
def host_ip():
    """IP adresa, cez ktoru ide predvolena cesta (nic sa neposiela)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 9))
            return sk.getsockname()[0]
    except OSError:
        return None


def wifi_signal():
    """Kvalita Wi-Fi spojenia v % z /proc/net/wireless (max 70 = 100 %)."""
    try:
        for line in open("/proc/net/wireless"):
            if ":" in line:
                quality = float(line.split(":", 1)[1].split()[1].rstrip("."))
                return min(100, round(quality * 100 / 70))
    except (OSError, ValueError, IndexError):
        pass
    return None


def host_loop(link, stop):
    while not stop.is_set():
        ip, wifi = host_ip(), wifi_signal()
        info = {}
        if ip:
            # "IP " na zaciatku: inak by Domoticz adresu vzal ako cislo (192.168)
            info["host_ip"] = f"IP {ip}"
        if wifi is not None:
            info["host_wifi"] = str(wifi)
        link.update(info)
        stop.wait(HOST_INTERVAL)


# ---------------------------------------------------------------------------
# TCP server pre cmd_st480.py
# ---------------------------------------------------------------------------
def check_command(text: str, stop: threading.Event):
    """Overi prikaz od klienta. Vrati (ramec alebo None, odpoved)."""
    try:
        frame = bytes.fromhex(text)
    except ValueError:
        return None, "ERR neplatny hex"
    if len(frame) < 8 or len(frame) % 4 or frame[-4:-2] != END:
        return None, "ERR zly format ramca"
    body = frame[:-4]
    if crc16_mcrf4xx(body) != int.from_bytes(frame[-2:], "big"):
        return None, "ERR zle CRC"
    if body == b"\xff\xff\xff\xff":
        log.info("Prijaty prikaz na ukoncenie")
        stop.set()
        return None, "OK koncim"
    if body[:4] != HDR + ADDR_ALL.to_bytes(2, "big") or len(body) == 4:
        return None, "ERR zla hlavicka alebo prazdny prikaz"
    for i in range(4, len(body), 4):
        reg = int.from_bytes(body[i:i + 2], "big")
        val = int.from_bytes(body[i + 2:i + 4], "big")
        if val not in ALLOWED_WRITES.get(reg, ()):
            return None, f"ERR zapis {reg:04x}={val:04x} nie je povoleny"
    return frame, "OK zaradene"


def read_request(conn) -> str:
    data = b""
    while len(data) < 1024 and b"\n" not in data:
        chunk = conn.recv(1024)
        if not chunk:
            break
        data += chunk
    return data.decode("ascii", "replace").strip().lower()


def tcp_server(srv: socket.socket, cmd_q: queue.Queue, stop: threading.Event):
    srv.settimeout(1.0)
    while not stop.is_set():
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        with conn:
            conn.settimeout(5)
            try:
                text = read_request(conn)
                log.info("Prikaz od %s: %s", addr[0], text)
                frame, reply = check_command(text, stop)
                if frame:
                    cmd_q.put(frame)
            except Exception as ex:
                reply = f"ERR {ex}"
            if not reply.startswith("OK"):
                log.warning("Prikaz odmietnuty: %s", reply)
            try:
                conn.sendall((reply + "\n").encode())
            except OSError:
                pass
    srv.close()


# ---------------------------------------------------------------------------
# Seriova linka
# ---------------------------------------------------------------------------
def serial_loop(port, decoder, link, cmd_q, stop):
    parser = FrameParser()
    while not stop.is_set():
        try:
            with serial.serial_for_url(port, baudrate=BAUDRATE, timeout=0.05) as ser:
                log.info("Seriovy port %s otvoreny", port)
                parser.reset()
                last_rx = time.monotonic()
                poll_open = False    # prave prisla vyzva a nikto neodpovedal
                while not stop.is_set():
                    data = ser.read(ser.in_waiting or 1)
                    now = time.monotonic()
                    if data:
                        last_rx = now
                        for frame in parser.feed(data):
                            poll_open = is_poll(frame)
                            link.update(handle_frame(frame, decoder))
                        continue

                    idle = now - last_rx
                    if parser.pending() and idle > PARTIAL_TIMEOUT:
                        log.warning("Zahadzujem neukonceny ramec: %s", parser.buf.hex())
                        parser.reset()
                    if parser.pending():
                        continue

                    since = cmd_q.oldest_since()
                    if since is None:
                        continue
                    if SEND_AFTER_POLL:
                        after_poll = poll_open and idle > POLL_REPLY_DELAY
                        fallback = idle > IDLE_GAP and now - since > POLL_FALLBACK
                    else:
                        after_poll, fallback = False, idle > IDLE_GAP
                    if not (after_poll or fallback):
                        continue

                    frame = cmd_q.pop()
                    ser.write(frame)
                    ser.flush()
                    log.info("Odoslany prikaz do kotla%s: %s",
                             " (po vyzve)" if after_poll else "", frame.hex())
                    link.sent(frame)
                    poll_open = False
                    last_rx = time.monotonic()
        except (serial.SerialException, OSError) as ex:
            log.error("Chyba serioveho portu: %s - skusim znova o 5 s", ex)
            stop.wait(5)


def replay(path, decoder, link):
    """Prehra zaznam zo st480_raw.log (cas + hex na riadok)."""
    parser = FrameParser()
    count = 0
    for line in open(path):
        parts = line.split()
        if not parts:
            continue
        for frame in parser.feed(bytes.fromhex(parts[-1])):
            count += 1
            link.update(handle_frame(frame, decoder))
    log.info("Hotovo: %d ramcov", count)


# ---------------------------------------------------------------------------
def setup_logging(logfile, verbose):
    if logfile == "-":
        handler = logging.StreamHandler()
    else:
        handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        handlers=[handler])


def main():
    ap = argparse.ArgumentParser(description="ST480 -> MQTT gateway")
    ap.add_argument("--port", default=SERIAL_PORT, help="seriovy port")
    ap.add_argument("--log", default="/tmp/st480.log",
                    help="subor logu (default v RAM), '-' = konzola")
    ap.add_argument("--dry-run", action="store_true",
                    help="bez MQTT, len logovat co by sa poslalo")
    ap.add_argument("--replay", metavar="SUBOR",
                    help="prehrat zaznam zo st480_raw.log (implikuje --dry-run)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug log")
    args = ap.parse_args()

    setup_logging("-" if args.replay else args.log, args.verbose)
    decoder = Decoder()
    cmd_q = CommandQueue()
    link = MqttLink(cmd_q, dry_run=args.dry_run or bool(args.replay))

    if args.replay:
        replay(args.replay, decoder, link)
        return

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    srv = socket.create_server((LISTEN_HOST, LISTEN_PORT))
    threading.Thread(target=tcp_server, args=(srv, cmd_q, stop),
                     name="tcp", daemon=True).start()
    link.start()
    threading.Thread(target=host_loop, args=(link, stop), name="host",
                     daemon=True).start()
    log.info("Gateway spusteny")

    try:
        serial_loop(args.port, decoder, link, cmd_q, stop)
    finally:
        link.stop()
        log.info("Gateway ukonceny")


if __name__ == "__main__":
    main()
