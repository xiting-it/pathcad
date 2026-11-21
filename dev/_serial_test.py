"""F1 串口模块的单元测试（不依赖硬件）。"""

from app.serial_f1 import (
    BYPASS_CRC,
    ERROR_CODES,
    MAX_BODY,
    build_frame,
    crc16_ccitt_false,
    parse_response,
)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"[OK  ] {name} {detail}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILS.append(name)


def main():
    # ── CRC 算法（对照协议文档示例）──
    check("CRC G01 示例", crc16_ccitt_false(b"G01 X10.5 Y-3.2 F800") == 0xC692,
          f"{crc16_ccitt_false(b'G01 X10.5 Y-3.2 F800'):04X} (期望 C692)")
    check("CRC E00 示例", crc16_ccitt_false(b"E00") == 0x0907,
          f"{crc16_ccitt_false(b'E00'):04X} (期望 0907)")
    check("CRC G00 三轴", crc16_ccitt_false(b"G00 X100 Y50 Z10") == 0xD0BA,
          f"{crc16_ccitt_false(b'G00 X100 Y50 Z10'):04X} (期望 D0BA)")
    check("CRC M50", crc16_ccitt_false(b"M50") == 0x5F53,
          f"{crc16_ccitt_false(b'M50'):04X} (实测板子接受 5F53)")

    # ── 帧构造 ──
    check("帧格式 M50", build_frame("M50") == b"@M50*5F53\r", f"{build_frame('M50')!r}")
    check("帧格式旁路", build_frame("M50", bypass_crc=True) == b"@M50*FFFF\r")
    check("帧含 CR", build_frame("G28").endswith(b"\r"))
    check("帧以 @ 开头", build_frame("G28").startswith(b"@"))

    # 超长正文要报错
    try:
        build_frame("G01 " + "X" * 90)
        check("超长正文抛错", False, "没有抛异常")
    except ValueError:
        check("超长正文抛错", True)
    check("正文长度上限", MAX_BODY == 89)

    # ── 应答解析 ──
    r = parse_response("E00*0907")
    check("解析 E00", r.code == "E00" and r.ok and r.crc_ok, f"code={r.code} ok={r.ok}")

    r = parse_response("E07*79E0")
    check("解析 E07", r.code == "E07" and not r.ok and r.crc_ok, f"{r.code} {r.desc}")
    check("E07 是系统忙", "系统忙" in r.desc, r.desc)

    # 所有错误码都要能解析
    for code, desc in ERROR_CODES.items():
        crc = "%04X" % crc16_ccitt_false(code.encode())
        rr = parse_response(f"{code}*{crc}")
        if code == "E00":
            check(f"错误码 {code}", rr.ok and rr.desc == desc, rr.desc)
        else:
            check(f"错误码 {code}", not rr.ok and rr.desc == desc, rr.desc)

    # CRC 错误要能识别
    r = parse_response("E00*0000")
    check("识别 CRC 错误", r.code == "E00" and not r.crc_ok, f"crc_ok={r.crc_ok}")

    # ── 状态帧解析 ──
    body = "D L000 R000 P 0.00 0.00 0.00"
    crc = "%04X" % crc16_ccitt_false(body.encode())
    r = parse_response(f"{body}*{crc}")
    check("解析状态帧", r.is_status and r.ok, f"is_status={r.is_status}")

    body = "D L101 R010 P 12.50 -3.25 0.00"
    crc = "%04X" % crc16_ccitt_false(body.encode())
    r = parse_response(f"{body}*{crc}")
    check("状态帧 限位左", r.limits_left == (1, 0, 1), f"{r.limits_left}")
    check("状态帧 限位右", r.limits_right == (0, 1, 0), f"{r.limits_right}")
    check("状态帧 负坐标", r.pos == (12.5, -3.25, 0.0), f"{r.pos}")

    body = "D L000 R000 P -6000.00 -6000.00 -6000.00"
    crc = "%04X" % crc16_ccitt_false(body.encode())
    r = parse_response(f"{body}*{crc}")
    check("状态帧 大负数", r.pos[0] == -6000.0, f"{r.pos}")

    # ── 异常输入 ──
    check("空串", parse_response("") .raw == "")
    check("垃圾数据不崩", parse_response("hello").code == "", "无异常")
    check("缺 CRC 段", parse_response("E00").code == "E00", "仍能取到码")

    print()
    if FAILS:
        print(f"{len(FAILS)} 项失败: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
