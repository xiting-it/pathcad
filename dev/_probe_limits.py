"""探测板子实际的软限位边界。

方法：二分法找一个轴上「能被接受的最大坐标」。
E010 是**下发前预检**，越界行不会产生运动，所以探测是安全的。
电机目前空载，即使走进合法范围也不会顶到机械结构。
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in "EB" and t[1:].isdigit()


def ask(s: socket.socket, cmd: str, wait: float = 12.0) -> str:
    s.settimeout(0.12)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass
    s.settimeout(1.0)
    s.sendto(cmd.encode() + CR, (IP, PORT))
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


def ok(s: socket.socket, axis: str, val: float) -> bool:
    cmd = f"G1 {axis}{val:g} F3000"
    return ask(s, cmd) == "E000"


def probe(s: socket.socket, axis: str, lo: float, hi: float) -> float:
    """返回该轴正方向能被接受的最大值（lo 一定能过，hi 一定不能）。"""
    if ok(s, axis, hi):
        return hi
    for _ in range(12):
        mid = (lo + hi) / 2
        if abs(hi - lo) < 0.5:
            break
        if ok(s, axis, mid):
            lo = mid
        else:
            hi = mid
    return lo


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    print("=" * 56)
    print(" 探测软限位（正方向）")
    print("=" * 56)

    for ax in "XYZ":
        # 先回到 0
        ask(s, f"G1 {ax}0 F3000")
        time.sleep(0.3)
        v = probe(s, ax, 0.0, 2000.0)
        print(f"  {ax} 正方向上限 ≈ {v:.1f}")
        ask(s, f"G1 {ax}0 F3000")
        time.sleep(0.2)

    print()
    print("=" * 56)
    print(" 探测软限位（负方向）")
    print("=" * 56)
    for ax in "XYZ":
        ask(s, f"G1 {ax}0 F3000")
        time.sleep(0.3)
        # 负方向：先试 -1 能不能过
        if not ok(s, ax, -1.0):
            print(f"  {ax} 负方向：连 -1 都不接受 → 可能不允许负坐标")
            continue
        v = probe_neg(s, ax)
        print(f"  {ax} 负方向上限 ≈ {v:.1f}")
        ask(s, f"G1 {ax}0 F3000")
        time.sleep(0.2)

    s.close()


def probe_neg(s: socket.socket, axis: str) -> float:
    lo, hi = 0.0, -2000.0
    if ok(s, axis, hi):
        return hi
    for _ in range(12):
        mid = (lo + hi) / 2
        if abs(hi - lo) < 0.5:
            break
        if ok(s, axis, mid):
            lo = mid
        else:
            hi = mid
    return lo


if __name__ == "__main__":
    main()
