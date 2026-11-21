"""板子自诊断：查驱动是否供电、轴是否可读。

重点看 M154 的 state 字段 —— 协议说「轴读不到」会走执行层失败，
那通常是电机供电（VMOT）缺失或驱动 SPI 链断了。
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

    print("=" * 68)
    print(" 板子自诊断")
    print("=" * 68)

    checks = [
        ("M154", "详细位置 + state（看 state 有没有报错）"),
        ("M120", "IO 状态（IOIN / IOORG / IOOUT）"),
        ("M114", "工作坐标"),
        ("M92", "丝杆导程"),
    ]

    for cmd, desc in checks:
        print(f"\n>>> {cmd}  —— {desc}")
        rows = ask(s, cmd)
        if not rows:
            print("    [无回包]")
            continue
        for r in rows:
            mark = ""
            if is_code(r):
                mark = "  <<< 结果码"
                if r not in ("E000", "B000"):
                    mark = "  <<< 非成功码！"
            elif r.startswith(("queued",)):
                continue
            elif r == cmd:
                continue
            print(f"    {r}{mark}")

    print("\n" + "=" * 68)
    print(" 判断依据")
    print("=" * 68)
    print("""
 · M154 的 state 字段如果报轴读不到 → 电机供电或驱动 SPI 断
 · 如果四个轴都能正常读回但电机不转 → 大概率是 VMOT 没电（保险丝/电源）
 · 位置坐标照样变化 → 这是正常的，开环板只按脉冲计数，不反映真实转动
""")

    s.close()


if __name__ == "__main__":
    main()
