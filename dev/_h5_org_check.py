"""只看原点开关状态，不发任何移动指令。

用法：运行后 40 秒内，请你依次用手按下各轴的原点开关，
每次按 2 秒再松开，程序会记录哪一位发生了变化。

如果 40 秒内一位都不动 → 原点开关没接线，绝对不能发 G28。
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"
DURATION = 40.0


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in "EB" and t[1:].isdigit()


def read_org(s: socket.socket) -> tuple[int, str]:
    s.settimeout(0.12)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass
    s.settimeout(1.2)
    s.sendto(b"M120" + CR, (IP, PORT))
    t0 = time.perf_counter()
    buf = ""
    while time.perf_counter() - t0 < 2.0:
        try:
            d, _ = s.recvfrom(4096)
        except socket.timeout:
            if "IOORG" in buf:
                break
            continue
        buf += d.decode("ascii", "replace")
        if "IOORG" in buf:
            break
    for line in buf.split("\r"):
        line = line.strip()
        if line.startswith("IOORG"):
            m = line.split()
            if len(m) >= 2:
                try:
                    return int(m[1], 16), line
                except ValueError:
                    pass
    return -1, "(读不到)"


def bits(v: int) -> str:
    names = ["X", "Y", "Z", "E"]
    return "  ".join(f"{n}={'●' if v & (1 << i) else '○'}" for i, n in enumerate(names))


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    base, _ = read_org(s)
    if base < 0:
        print("[!!] 板子没回 IOORG —— 先检查连接")
        s.close()
        return

    print("=" * 62)
    print(" 原点开关验证（只看不动，不发任何移动指令）")
    print("=" * 62)
    print(f" 初始状态  IOORG 0x{base:02X}   {bits(base)}")
    print()
    print(" 请在接下来 40 秒内，依次用手按下各轴的原点开关")
    print(" （每次按住 2 秒再松开，顺序不限）")
    print()

    seen: dict[int, str] = {}
    last = base
    t0 = time.time()
    while time.time() - t0 < DURATION:
        v, _ = read_org(s)
        if v < 0:
            continue
        if v != last:
            changed = v ^ last
            names = []
            for i, n in enumerate(["X", "Y", "Z", "E"]):
                if changed & (1 << i):
                    names.append(f"{n}→{'按下' if v & (1 << i) else '松开'}")
            ev = "，".join(names)
            print(f"  [{time.time() - t0:5.1f}s]  IOORG 0x{v:02X}   {bits(v)}   {ev}")
            for i, n in enumerate(["X", "Y", "Z", "E"]):
                if changed & (1 << i) and (v & (1 << i)):
                    seen[i] = n
            last = v
        time.sleep(0.25)

    print()
    print("=" * 62)
    print(" 结果")
    print("=" * 62)
    if not seen:
        print("  ❌ 40 秒内任何一位都没变化")
        print()
        print("     说明原点开关没接线、或接错、或完全没装。")
        print("     >>> 绝对不要发 G28 <<<")
        print("     否则电机会一直往负方向找原点，60 秒后超时。")
        print("     这 60 秒满电流堵转，几分钟就能烧毁电机绕组。")
    else:
        names = [seen[i] for i in sorted(seen)]
        print(f"  ✅ 检测到这些轴的原点开关有效：{', '.join(names)}")
        print()
        print(f"     没检测到的轴：{', '.join(n for i, n in enumerate(['X','Y','Z','E']) if i not in seen) or '无'}")
        print("     （没检测到的轴，回原点时不要勾选）")

    s.close()


if __name__ == "__main__":
    main()
