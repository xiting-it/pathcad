"""复现 E010：把当时那一整包 6 条原样重发，看能否再现。"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"

BATCH = [
    "G1 X-0.999 Y3.192 F600",
    "G1 X-0.956 Y2.997 F600",
    "G1 X-0.906 Y2.803 F600",
    "G1 X-0.848 Y2.612 F600",
    "G1 X-0.782 Y2.424 F600",
    "G1 X-0.71 Y2.238 F600",
]


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in "EB" and t[1:].isdigit()


def run_batch(s: socket.socket, label: str) -> list[str]:
    s.settimeout(0.15)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass

    payload = ("\r".join(BATCH) + "\r").encode()
    s.sendto(payload, (IP, PORT))

    codes: list[str] = []
    t0 = time.perf_counter()
    s.settimeout(1.0)
    while time.perf_counter() - t0 < 20:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            if len(codes) >= len(BATCH):
                break
            continue
        for p in d.decode("ascii", "replace").split("\r"):
            p = p.strip()
            if is_code(p):
                codes.append(p)
        if len(codes) >= len(BATCH):
            break

    mark = ""
    if any(c not in ("E000", "B000") for c in codes):
        mark = "   <<< 出现失败！"
    print(f"  {label:8s} 结果 {codes}{mark}")
    return codes


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    print("=" * 66)
    print(" 整包重发 5 次（每次 6 条同发）")
    print("=" * 66)

    bad = 0
    for i in range(5):
        codes = run_batch(s, f"第{i + 1}次")
        if any(c not in ("E000", "B000") for c in codes):
            bad += 1
        time.sleep(0.5)

    print()
    print(f"  5 次里有 {bad} 次出现失败")
    if bad == 0:
        print("  → 整包重发也不复现，说明和当时的具体时序/队列状态有关")
    else:
        print("  → 复现了！问题在批量发送本身")

    s.close()


if __name__ == "__main__":
    main()
