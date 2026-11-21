"""生成测试报告用的截图（终端风格）。"""

from __future__ import annotations

import json
import time

import serial
from PIL import Image, ImageDraw, ImageFont

PORT = "COM7"
W, H = 1100, 720
BG = (12, 14, 18)
FG = (200, 210, 220)
DIM = (110, 120, 135)
GREEN = (90, 209, 154)
ORANGE = (255, 180, 84)
RED = (255, 107, 107)
BLUE = (78, 163, 255)
CYAN = (110, 200, 220)


def get_font(size=15, bold=False):
    """取一个支持中文和常用符号的字体。

    优先雅黑（msyh），它同时有中文和 ✓→←● 这些符号。
    粗体用 msyhbd。
    """
    import os
    base = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "Deng.ttf", "simhei.ttf"]
    for n in names:
        p = os.path.join(base, n)
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_terminal(lines, path, title="LCSystemF1 串口通信"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f = get_font(15)
    fb = get_font(15, bold=True)
    fs = get_font(13)

    # 标题栏
    d.rectangle([0, 0, W, 34], fill=(30, 34, 41))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([12 + i * 20, 11, 24 + i * 20, 23], fill=c)
    d.text((W // 2 - 130, 9), title, font=fb, fill=FG)

    y = 48
    for text, color in lines:
        if y > H - 24:
            break
        d.text((18, y), text, font=f, fill=color)
        y += 22
    img.save(path)
    print(f"  -> {path}")


def main():
    # ── 截图 1：完整收发记录 ──
    ser = serial.Serial(PORT, 115200, timeout=3)
    time.sleep(0.5)

    def cmd(g, wait=0.3):
        ser.reset_input_buffer()
        ser.write(f"@{g}*FFFF\r".encode())
        ser.flush()
        time.sleep(wait)
        data = ser.read_until(b"\r")
        return data[:-1].decode("ascii", errors="replace") if data else "(超时)"

    lines = []
    lines.append(("[*] 已连接 COM7  @ 115200 8N1    CRC 旁路 FFFF", DIM))
    lines.append(("", FG))

    steps = [
        ("M50", 0.3, "查询状态"),
        ("G00 X0 Y0 Z0", 0.4, "回原点"),
        ("G01 X20 Y15 F600", 1.5, "直线插补 X20 Y15"),
        ("M50", 0.3, "确认位置"),
        ("G02 X0 Y15 I-10 J-7.5 F600", 3.0, "圆弧（I/J）"),
        ("M50", 0.3, "确认圆弧终点"),
        ("G99", 0.2, "错误指令"),
        ("G01 X", 0.2, "语法错误"),
        ("G00 X9999", 0.2, "超行程"),
        ("G02 X10 Y10 F600", 0.2, "圆弧缺 I/J/R"),
        ("G00 X0 Y0 Z0", 0.4, "回原点"),
        ("M50", 0.3, "最终状态"),
    ]

    for g, wait, desc in steps:
        lines.append((f">>  @{g}*FFFF\\r", BLUE))
        r = cmd(g, wait)
        if r.startswith("E00"):
            col = GREEN
        elif r.startswith("D "):
            col = CYAN
        elif r.startswith("E0"):
            col = RED
        else:
            col = ORANGE
        lines.append((f"<<  {r}          {('-- ' + desc) if desc else ''}", col))
        lines.append(("", FG))

    ser.close()
    draw_terminal(lines, "_shots/f1/01-serial-session.png")

    # ── 截图 2：速度测试结果表 ──
    img = Image.new("RGB", (W, 520), BG)
    d = ImageDraw.Draw(img)
    fb = get_font(17, bold=True)
    f = get_font(15)
    fm = get_font(14)

    d.rectangle([0, 0, W, 34], fill=(30, 34, 41))
    d.text((W // 2 - 120, 9), "速度与加速度测试", font=fb, fill=FG)

    y = 56
    d.text((24, y), "结论：无加减速，耗时精确等于「距离 ÷ 速度」", font=fb, fill=GREEN)
    y += 40

    headers = ["F (mm/min)", "标称 (mm/s)", "实测耗时", "实测 (mm/s)", "效率"]
    cols = [24, 200, 380, 540, 760]
    for i, h in enumerate(headers):
        d.text((cols[i], y), h, font=fm, fill=DIM)
    y += 26
    d.line([24, y, W - 24, y], fill=(50, 56, 66))
    y += 12

    rows = [
        ("60", "1.00", "50.00 s", "1.00", "100.0%"),
        ("120", "2.00", "24.99 s", "2.00", "100.0%"),
        ("300", "5.00", "10.00 s", "5.00", "100.0%"),
        ("600", "10.00", "5.00 s", "9.99", "99.9%"),
        ("1200", "20.00", "2.50 s", "19.97", "99.8%"),
        ("3000", "50.00", "1.00 s", "49.77", "99.5%"),
        ("6000", "100.00", "0.51 s", "98.98", "99.0%"),
    ]
    for row in rows:
        for i, v in enumerate(row):
            col = FG if i < 4 else GREEN
            d.text((cols[i], y), v, font=f, fill=col)
        y += 27

    y += 16
    d.text((24, y), "短线段开销：10 段 × 5mm = 5.05s，1 段 × 50mm = 5.05s → 无额外开销", font=fm, fill=ORANGE)

    img.save("_shots/f1/02-speed-test.png")
    print("  -> _shots/f1/02-speed-test.png")

    # ── 截图 3：圆弧对比 ──
    img = Image.new("RGB", (W, 560), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 34], fill=(30, 34, 41))
    d.text((W // 2 - 140, 9), "圆弧 vs 直线离散（半径10 整圆）", font=fb, fill=FG)

    y = 56
    d.text((24, y), "结论：G02 圆弧比直线离散慢 2.7 倍 → 不用圆弧", font=fb, fill=RED)
    y += 40

    headers = ["F", "理论", "G02 圆弧", "32段直线", "64段直线"]
    cols = [24, 160, 320, 520, 700]
    for i, h in enumerate(headers):
        d.text((cols[i], y), h, font=fm, fill=DIM)
    y += 26
    d.line([24, y, W - 24, y], fill=(50, 56, 66))
    y += 12

    rows = [
        ("300", "12.57s", "34.04s (2.71×)", "11.52s (0.92×)", "11.72s (0.93×)"),
        ("600", "6.28s", "17.09s (2.72×)", "5.86s (0.93×)", "6.07s (0.97×)"),
        ("1200", "3.14s", "8.61s (2.74×)", "3.04s (0.97×)", "3.25s (1.04×)"),
        ("3000", "1.26s", "3.52s (2.80×)", "1.34s (1.07×)", "1.56s (1.24×)"),
    ]
    for row in rows:
        for i, v in enumerate(row):
            col = [FG, DIM, RED, GREEN, GREEN][i]
            d.text((cols[i], y), v, font=f, fill=col)
        y += 30

    y += 18
    d.text((24, y), "圆弧精度测试：4 象限点半径均为 10.000mm，误差 0.0000mm", font=fm, fill=GREEN)
    y += 26
    d.text((24, y), "但固件的微线段逼近过于保守，实际耗时是理论的 2.7 倍", font=fm, fill=ORANGE)

    img.save("_shots/f1/03-arc-compare.png")
    print("  -> _shots/f1/03-arc-compare.png")


if __name__ == "__main__":
    main()
