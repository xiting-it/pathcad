"""复现 E010：把失败的那几条指令逐条单独发，看哪条被拒。"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in "EB" and t[1:].isdigit()


def ask(s: socket.socket, line: str, wait: float = 12.0) -> str:
    s.settimeout(0.15)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass
    s.settimeout(1.5)
    s.sendto(line.encode() + CR, (IP, PORT))
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < wait:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        for p in d.decode("ascii", "replace").split("\r"):
            p = p.strip()
            if is_code(p):
                return p
    return "超时"


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    # 出错那条前后连续的 6 条，来自日志
    seq = [
        "G1 X-1.093 Y4.384 F600",
        "G1 X-1.097 Y4.184 F600",
        "G1 X-1.093 Y3.985 F600",
        "G1 X-1.081 Y3.785 F600",
        "G1 X-0.999 Y3.192 F600",
        "G1 X-0.956 Y2.997 F600",   # ← 报 E010 的那条
        "G1 X-0.906 Y2.803 F600",
        "G1 X-0.848 Y2.612 F600",
    ]

    print("=" * 62)
    print(" 逐条单独发送，看哪条被拒")
    print("=" * 62)
    for cmd in seq:
        code = ask(s, cmd)
        mark = "  <<< 被拒！" if code not in ("E000", "B000") else ""
        print(f"  {code:5s}  {cmd}{mark}")
        time.sleep(0.3)

    print()
    print("=" * 62)
    print(" 换几种写法再试同一条")
    print("=" * 62)
    variants = [
        "G1 X-0.956 Y2.997 F600",
        "G1 X-0.956 Y2.997 F1200",
        "G1 X-0.9560 Y2.9970 F600",
        "G1 Y2.997 F600",
        "G1 X-0.95 Y3.0 F600",
    ]
    for cmd in variants:
        code = ask(s, cmd)
        print(f"  {code:5s}  {cmd}")
        time.sleep(0.3)

    s.close()


if __name__ == "__main__":
    main()
