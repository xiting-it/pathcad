"""逐轴读 M154，看每一路驱动能不能单独读回。

协议说「执行层失败：轴读不到 → 停所有轴」。
如果某一路驱动报故障，板子可能出于保护把四轴全停了。
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"
LETTERS = ("E", "B")


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in LETTERS and t[1:].isdigit()


def ask(s: socket.socket, cmd: str, wait: float = 3.0) -> list[str]:
    s.settimeout(0.15)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass
    s.settimeout(wait)
    s.sendto(cmd.encode() + CR, (IP, PORT))
    rows: list[str] = []
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            if rows:
                break
            continue
        for p in d.decode("ascii", "replace").split("\r"):
            p = p.strip()
            if p:
                rows.append(p)
    return rows


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    print("=" * 66)
    print(" 逐轴驱动读取（M154 P0~P3）")
    print("=" * 66)

    for i, ax in enumerate("XYZE"):
        rows = ask(s, f"M154 P{i}")
        data = [r for r in rows if not is_code(r) and r not in ("queued",)]
        code = next((r for r in rows if is_code(r)), "无")
        ok = "OK " if code in ("E000", "B000") else "!! "
        print(f"  {ok}P{i} ({ax})  {code:5s}  {' '.join(data)[:64]}")

    print()
    print("=" * 66)
    print(" 让电机通电并保持（M17 使能）")
    print("=" * 66)
    rows = ask(s, "M17")
    code = next((r for r in rows if is_code(r)), "无")
    print(f"  M17 → {code}")
    print("  现在四个电机应该都有保持力矩（用手拧轴拧不动）")

    s.close()


if __name__ == "__main__":
    main()
