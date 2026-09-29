#!/usr/bin/env python3
"""Posle prikaz do ST480 gateway (st480_gw.py)."""

import argparse
import socket
import sys

COMMANDS = {
    "l": ("Rezim: Letny", 0x0245, 3),
    "p": ("Rezim: Paralelne cerpadla", 0x0245, 2),
    "b": ("Rezim: Priorita TUV", 0x0245, 1),
    "k": ("Rezim: Kurenie", 0x0245, 0),
    "q": ("Ukoncenie gateway", None, None),
}


def crc16_mcrf4xx(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def build_frame(reg, val) -> bytes:
    if reg is None:
        body = b"\xff\xff\xff\xff"
    else:
        body = b"\x02\x26\x00\x00" + reg.to_bytes(2, "big") + val.to_bytes(2, "big")
    return body + b"\x02\x18" + crc16_mcrf4xx(body).to_bytes(2, "big")


def main():
    ap = argparse.ArgumentParser(
        description="Posle prikaz do ST480 gateway.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="prikazy:\n" + "\n".join(f"  {k}  {v[0]}" for k, v in COMMANDS.items()))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("-c", choices=COMMANDS, help="prikaz")
    g.add_argument("-w", nargs=2, metavar=("REG", "HODNOTA"),
                   help="zapis registra, napr. -w 01f6 58 (REG hex, HODNOTA desiatkovo)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=50080)
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="len vypise ramec, nic neposle")
    args = ap.parse_args()

    if args.w:
        reg, val = int(args.w[0], 16), int(args.w[1])
        name = f"Zapis {reg:04x} = {val}"
    else:
        name, reg, val = COMMANDS[args.c]
    frame = build_frame(reg, val).hex()
    print(f"{name}: {frame}")
    if args.dry_run:
        return 0

    try:
        with socket.create_connection((args.host, args.port), timeout=5) as s:
            s.sendall(frame.encode("ascii"))
            s.shutdown(socket.SHUT_WR)      # koniec poziadavky
            reply = s.recv(1024).decode(errors="replace").strip()
    except OSError as e:
        print(f"Chyba spojenia s gateway: {e}", file=sys.stderr)
        return 1

    print(f"Gateway: {reply or '(bez odpovede)'}")
    return 0 if reply.startswith("OK") else 1


if __name__ == "__main__":
    sys.exit(main())
