"""与开发板通信：UDP 下发器 + 模拟板。

协议依据 docs/00-共用内核.md 与 _proto.txt：
  - UDP，板端 192.168.0.102:5000
  - 行尾 \\r，单行 ≤128B，单包 ≤1472B
  - 异步模式回：queued #N → echo → 之后单独一个 E000
  - 结果包不带票号，必须一发一等

设计：真机和模拟板共用同一套「发一行、等 E000」的串行策略，
所以模拟板上测通的流程，换成真机 IP 就能直接跑。
"""

from __future__ import annotations

import math
import socket
import threading
import time
from dataclasses import dataclass, field

from .logger import (
    SRC_ERR,
    SRC_JOB,
    SRC_MOCK,
    SRC_UDP_RX,
    SRC_UDP_TX,
    get_logger,
)

log = get_logger()

# ══════════════════════════════════════════════════════════
# 通用
# ══════════════════════════════════════════════════════════

DEFAULT_IP = "192.168.0.102"
DEFAULT_PORT = 5000
MOCK_IP = "127.0.0.1"

MAX_LINE = 128
MAX_PACKET = 1472
# 协议的行结束符。用常量而不是字面量，免得在多层字符串拼接里被吃掉。
CR = chr(13)


# 结果码首字母：R7 全用 E，H5 是 G 行 E、纯 M 行 B。
# 下发器不区分指令类型，一律认这两个字母，配对逻辑不用改。
REPLY_LETTERS = ("E", "B")


def _is_result_code(text: str) -> bool:
    """是不是最终结果码（如 E000 / B020）。"""
    return len(text) == 4 and text[0] in REPLY_LETTERS and text[1:].isdigit()


def _is_ok_code(code: str) -> bool:
    """是不是成功码（E000 / B000）。"""
    return code in ("E000", "B000")


def _est_move_ms(line: str, cur_speed: float, accel: float = 300.0) -> float:
    """粗略估算一条运动指令的耗时（毫秒），用于模拟板。"""
    up = line.upper()
    if not (up.startswith("G0") or up.startswith("G1")):
        return 0.0
    dist = 0.0
    feed = cur_speed
    vals: dict[str, float] = {}
    for tok in up.split():
        if len(tok) < 2:
            continue
        try:
            v = float(tok[1:])
        except ValueError:
            continue
        vals[tok[0]] = v
    for ax in ("X", "Y", "Z"):
        if ax in vals:
            dist = max(dist, abs(vals[ax]))
    if "F" in vals:
        feed = vals["F"]
    if dist <= 0 or feed <= 0:
        return 0.0
    v = feed / 60.0  # mm/s
    d_acc = v * v / accel
    if dist >= d_acc:
        t = dist / v + v / accel
    else:
        vpk = math.sqrt(dist * accel)
        t = 2.0 * vpk / accel
    return t * 1000.0


# ══════════════════════════════════════════════════════════
# 模拟板
# ══════════════════════════════════════════════════════════


@dataclass
class MockState:
    running: bool = False
    port: int = DEFAULT_PORT
    queue: list = field(default_factory=list)  # [(line, finish_time)]
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # 回码风格：
    #   "E"  所有行都回 E 码（旧 R7 网口板）
    #   "EB" G 行回 E、纯 M 行回 B（H5，默认，与当前主力板一致）
    reply_style: str = "EB"
    io_out: list[int] = field(default_factory=lambda: [0] * 8)
    io_events: list[tuple[float, int, int]] = field(default_factory=list)
    lines_done: int = 0
    errors: int = 0
    last_error: str = ""
    feed: float = 1500.0
    accel: float = 300.0
    busy_until: float = 0.0
    started_at: float = 0.0
    speed_scale: float = 1.0  # >1 = 模拟板跑得更快（缩短等待时间）
    lock: threading.Lock = field(default_factory=threading.Lock)


class MockBoard:
    """照着协议文档假扮那块板子。监听 127.0.0.1:5000。"""

    def __init__(self, port: int = DEFAULT_PORT):
        self.state = MockState(port=port)
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> tuple[bool, str]:
        if self.state.running:
            return True, "已经在运行"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((MOCK_IP, self.state.port))
            s.settimeout(0.2)
        except OSError as e:
            msg = f"端口 {self.state.port} 绑定失败：{e}（可能被占用或已有真机连接）"
            log.error(SRC_ERR, msg)
            return False, msg

        self.sock = s
        self._stop.clear()
        self.state.running = True
        self.state.queue.clear()
        self.state.pos = (0.0, 0.0, 0.0)
        self.state.io_out = [0] * 8
        self.state.io_events = []
        self.state.lines_done = 0
        self.state.errors = 0
        self.state.busy_until = 0.0
        self.state.started_at = time.time()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        msg = f"模拟板已启动，监听 {MOCK_IP}:{self.state.port}（速度 ×{self.state.speed_scale:g}）"
        log.info(SRC_MOCK, msg)
        return True, msg

    def stop(self) -> tuple[bool, str]:
        if not self.state.running:
            return True, "没在运行"
        self._stop.set()
        self.state.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        log.info(SRC_MOCK, f"模拟板已停止（共处理 {self.state.lines_done} 行）")
        return True, "模拟板已停止"

    # ── 内部 ──

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(data, addr)
            except Exception as e:  # 模拟板自身出错不能让线程死
                self.state.last_error = f"mock internal: {e}"

    def _handle(self, data: bytes, addr):
        # 剥 6 字节帧头（A5 5A + seq）
        payload = data
        if len(payload) >= 6 and payload[0] == 0xA5 and payload[1] == 0x5A:
            payload = payload[6:]
        text = payload.decode("utf-8", errors="replace")
        lines = [ln for ln in text.replace("\n", "\r").split("\r") if ln.strip()]
        if not lines:
            return

        log.debug(SRC_MOCK, f"收到 {len(data)}B：{text.replace(chr(13), chr(92)+'r')}", raw=data)

        results: list[tuple[str, float, str]] = []  # (E码, 完成时刻, 详情)
        for raw in lines:
            line = raw.split(";")[0].strip()
            if not line:
                continue
            code, finish = self._exec(line)
            code = self._fmt_code(line, code)
            detail = self._detail(line, code)
            results.append((code, finish, detail))
            if not (code == "E000" or code == "B000"):
                log.warn(SRC_MOCK, f"→ {code}  {line[:40]}")

        with self.state.lock:
            pending = len([q for q in self.state.queue if q[1] > time.time()])
        if results:
            # 注意 results 每项是 (code, finish, detail) 三元组，
            # 这里只需要下标，不能再按二元解包（会 ValueError 导致整个
            # _handle 抛异常、外层的 except 吞掉，表现为板子永不回包）。
            msg = "\r".join(f"queued #{i + 1} ({pending} pending)"
                            for i in range(len(results)))
            self._reply(addr, msg)
        # echo 包
        self._reply(addr, data.decode("utf-8", errors="replace"))

        # 结果包：等到这条指令真正执行完再回
        for code, finish, detail in results:
            payload = code + ((chr(13) + detail) if detail else "")
            delay = max(0.0, finish - time.time())
            if delay <= 0.001:
                self._reply(addr, payload)
            else:
                threading.Timer(delay, self._send_result, args=(addr, payload)).start()

    def _send_result(self, addr, payload: str):
        self._reply(addr, payload)

    def _detail(self, line: str, code: str) -> str:
        """按协议生成结果包的详情行（M114/M120 这类查询指令才有）。

        ⚠️ 不要在这里拿 self.state.lock：调用链 _handle 已经持有它，
        Python 的 Lock 不可重入，会直接死锁（表现为板子永不回包）。
        """
        if not (code == "E000" or code == "B000"):
            return ""
        up = line.upper()
        pos = self.state.pos
        io_out = list(self.state.io_out)

        if up.startswith("M114"):
            return (f"X:{pos[0]:.3f} Y:{pos[1]:.3f} Z:{pos[2]:.3f} E:0.000 "
                    + CR + "ok")

        if up.startswith("M120"):
            mask = 0
            for i, v in enumerate(io_out[:8]):
                if v:
                    mask |= (1 << i)
            bits = "".join(str((mask >> i) & 1) for i in range(7, -1, -1))
            return ("IOIN 0xFF 11111111" + CR
                    + "IOORG 0x40 01000000" + CR
                    + f"IOOUT 0x{mask:02X} {bits}" + CR + "ok")

        return ""

    def _reply(self, addr, text: str):
        if not self.sock:
            return
        try:
            self.sock.sendto((text + "\r").encode("utf-8"), addr)
        except OSError:
            pass

    def _fmt_code(self, line: str, code: str) -> str:
        """把 E 码换成实际该回的首字母。

        H5 的规则：含 G 字的行回 E，纯 M 行回 B，两者共用同一张状态码表。
        R7 一律回 E。
        """
        if self.state.reply_style != "EB":
            return code
        if not code or code[0] != "E" or len(code) < 4:
            return code
        up = line.upper()
        has_g = "G" in up.split()[0] if up.split() else False
        has_m = any(t.startswith("M") and t[1:2].isdigit() for t in up.split())
        # 有 G → E；无 G 且有 M → B
        if not has_g and has_m:
            return "B" + code[1:]
        return code

    def _exec(self, line: str) -> tuple[str, float]:
        """执行一行，返回 (E码, 预计完成时刻)。"""
        up = line.upper()
        st = self.state
        now = time.time()

        # 先清理已执行完的队列项（每条运动入队时记录预计完成时刻）
        now = time.time()
        with st.lock:
            st.queue = [q for q in st.queue if q[1] > now]
            if len(st.queue) >= 8:
                st.errors += 1
                st.last_error = "E020 队列满"
                return ("E020", now)

        if up.startswith("M121"):
            # 协议里 M121 有两种写法：
            #   M121 U<ch> V<0/1>   只改某一路
            #   M121 V<mask>        一次写全部 8 路（mask 的每一位对应一路）
            # 之前统一按「单路」处理，导致 M121 V255 只把第 0 路设成 255，
            # 结果 IOOUT 变成 0x01 而不是 0xFF。
            ch: int | None = None
            lv = 0
            flip = False
            for tok in up.split():
                try:
                    if tok.startswith("U") and len(tok) > 1:
                        ch = int(float(tok[1:]))
                    elif tok.startswith("V") and len(tok) > 1:
                        lv = int(float(tok[1:]))
                    elif tok.startswith("T") and len(tok) > 1:
                        flip = bool(int(float(tok[1:])))
                except ValueError:
                    pass

            if ch is None:
                # 批量写法：mask 的位 0..7 对应 8 路输出
                with st.lock:
                    for i in range(8):
                        st.io_out[i] = 1 if (lv >> i) & 1 else 0
                        st.io_events.append((time.time() - st.started_at, i, st.io_out[i]))
            else:
                if not (0 <= ch <= 7):
                    st.errors += 1
                    return ("E010", now)
                with st.lock:
                    if flip:
                        st.io_out[ch] = 0 if st.io_out[ch] else 1
                    else:
                        st.io_out[ch] = 1 if lv else 0
                    st.io_events.append((time.time() - st.started_at, ch, st.io_out[ch]))

            finish = self._advance(2.0)
            st.lines_done += 1
            return ("E000", finish)

        if up.startswith("M120"):
            finish = self._advance(2.0)
            st.lines_done += 1
            return ("E000", finish)

        if up.startswith("M114") or up.startswith("M154"):
            finish = self._advance(2.0)
            st.lines_done += 1
            return ("E000", finish)

        if up.startswith("G4"):
            # ⚠️ P 的单位随板子不同：
            #   H5（reply_style="EB"）→ 秒，P0.12 = 120ms
            #   R7 / Marlin        → 毫秒，P120 = 120ms
            # 之前一律按毫秒算，模拟 H5 时延时会差 1000 倍。
            p_is_seconds = self.state.reply_style == "EB"
            ms = 0.0
            for tok in up.split():
                try:
                    if tok.startswith("P"):
                        v = float(tok[1:])
                        ms = v * 1000.0 if p_is_seconds else v
                    elif tok.startswith("S"):
                        ms = float(tok[1:]) * 1000
                except ValueError:
                    pass
            finish = self._advance(ms)
            st.lines_done += 1
            return ("E000", finish)

        if up.startswith("M400") or up.startswith("M204") or up.startswith("M203") \
                or up.startswith("M92") or up.startswith("G21") or up.startswith("G90") \
                or up.startswith("G91") or up.startswith("G92") or up.startswith("G28"):
            for tok in up.split():
                try:
                    if up.startswith("M204") and tok.startswith("A"):
                        st.accel = float(tok[1:])
                    elif up.startswith("M203") and tok.startswith("V"):
                        st.feed = float(tok[1:])
                except ValueError:
                    pass
            finish = self._advance(2.0)
            st.lines_done += 1
            return ("E000", finish)

        if up.startswith("G0") or up.startswith("G1"):
            ms = _est_move_ms(line, st.feed, st.accel)
            # 单条 >60s → E010
            if ms > 60000:
                st.errors += 1
                st.last_error = "E010 单条运动超过 60s"
                return ("E010", now)
            # 软限位
            vals = {}
            for tok in up.split():
                try:
                    if tok[:1] in "XYZ":
                        vals[tok[0]] = float(tok[1:])
                    elif tok.startswith("F"):
                        st.feed = float(tok[1:])
                except ValueError:
                    pass
            nx = vals.get("X", st.pos[0])
            ny = vals.get("Y", st.pos[1])
            nz = vals.get("Z", st.pos[2])
            if abs(nx) > 1000 or abs(ny) > 1000 or abs(nz) > 500:
                st.errors += 1
                st.last_error = "E010 超软限位"
                return ("E010", now)
            with st.lock:
                st.pos = (nx, ny, nz)
                # 队列项的完成时刻要考虑 speed_scale，与 _advance 保持一致
                eff = ms / max(0.01, st.speed_scale) / 1000.0
                finish = max(now, st.busy_until) + eff
                st.queue.append((line, finish))
            done_at = self._advance(ms)
            st.lines_done += 1
            return ("E000", done_at)

        # 未知指令
        st.errors += 1
        st.last_error = f"E009 不支持：{line[:24]}"
        return ("E009", now)

    def _advance(self, ms: float) -> float:
        """把忙碌时间往后推，返回这条指令的完成时刻。

        speed_scale>1 时按比例缩短（模拟板跑得更快，方便验证长任务）。
        """
        now = time.time()
        eff = ms / max(0.01, self.state.speed_scale) / 1000.0
        with self.state.lock:
            base = max(now, self.state.busy_until)
            self.state.busy_until = base + eff
            return self.state.busy_until

    def snapshot(self) -> dict:
        st = self.state
        now = time.time()
        with st.lock:
            return {
                "running": st.running,
                "port": st.port,
                "pos": [round(v, 3) for v in st.pos],
                "io_out": list(st.io_out),
                "lines_done": st.lines_done,
                "errors": st.errors,
                "last_error": st.last_error,
                "queue_len": len([q for q in st.queue if q[1] > now]),
                "io_events": [[round(t, 3), ch, lv] for t, ch, lv in st.io_events[-400:]],
                "elapsed": round(now - st.started_at, 2) if st.running else 0.0,
            }


# ══════════════════════════════════════════════════════════
# 真机连接
# ══════════════════════════════════════════════════════════


class BoardLink:
    """向真板子发指令，一发一等 E000。"""

    def __init__(self, ip: str = DEFAULT_IP, port: int = DEFAULT_PORT):
        self.ip = ip
        self.port = port
        self.sock: socket.socket | None = None
        self.last_error = ""
        self.sent = 0
        self.ok = 0
        self.failed = 0

    def open(self) -> tuple[bool, str]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            self.sock = s
            log.info(SRC_UDP_TX, f"建立 UDP 套接字 → {self.ip}:{self.port}")
            # 用 M114 探活
            code, detail = self.ask("M114", timeout=2.0)
            if code == "E000" or code == "B000":
                msg = f"已连接 {self.ip}:{self.port}（{detail or 'ok'}）"
                log.info(SRC_UDP_RX, msg)
                return True, msg
            if code:
                msg = f"已连接 {self.ip}:{self.port}，探活返回 {code}"
                log.warn(SRC_UDP_RX, msg)
                return True, msg
            msg = f"连不上 {self.ip}:{self.port}（无回包）。检查网线、IP 网段、板子是否上电。"
            log.error(SRC_ERR, msg)
            return False, msg
        except OSError as e:
            msg = f"网络错误：{e}"
            log.error(SRC_ERR, msg)
            return False, msg

    def close(self):
        try:
            if self.sock:
                self.sock.close()
                log.info(SRC_UDP_TX, f"关闭 UDP 套接字（{self.ip}:{self.port}）")
        except Exception:
            pass
        self.sock = None

    def ask(self, line: str, timeout: float = 5.0) -> tuple[str, str]:
        """发一行，等 E 码。返回 (E码, 详情文本)。超时返回 ('', '')。"""
        if not self.sock:
            return "", "未连接"
        if len(line) > MAX_LINE:
            log.error(SRC_ERR, f"行超长 {len(line)}B > {MAX_LINE}B，未发送：{line[:40]}")
            return "E022", "行超长"
        payload = (line + "\r").encode("utf-8")
        try:
            self.sock.sendto(payload, (self.ip, self.port))
        except OSError as e:
            self.last_error = str(e)
            log.error(SRC_ERR, f"发送失败：{e}")
            return "", str(e)
        self.sent += 1
        log.debug(SRC_UDP_TX, line, raw=payload)
        deadline = time.time() + timeout
        details: list[str] = []
        while time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("utf-8", errors="replace")
            log.debug(SRC_UDP_RX, text.replace("\r", "\\r").replace("\n", "\\n"), raw=data)

            # ⚠️ 必须先把**整个包**扫完再决定返回。
            # 结果包的格式是 `E000\rIOIN ..\rIOORG ..\rIOOUT ..\rok\r`，
            # E000 在最前面；一旦见到 E000 就 return，后面的 IO 数据全丢。
            hit = ""
            for part in text.replace("\n", "\r").split("\r"):
                p = part.strip()
                if not p:
                    continue
                if _is_result_code(p):
                    hit = p
                    if _is_ok_code(p):
                        self.ok += 1
                    else:
                        self.failed += 1
                        self.last_error = p
                        log.warn(SRC_UDP_RX, f"{p}  ← {line[:40]}")
                    continue
                # queued #N 忽略；echo 等于指令本身也忽略
                if p.startswith("queued") or p == line.strip():
                    continue
                details.append(p)

            if hit:
                return hit, " ".join(details)

        log.warn(SRC_UDP_RX, f"等待回包超时（{timeout:g}s）：{line[:40]}")
        return "", " ".join(details)

    # ── 批量发送 ────────────────────────────────────────

    def ask_many(
        self,
        lines: list[str],
        timeout: float = 5.0,
        per_line_extra: float = 1.0,
    ) -> list[tuple[str, str]]:
        """一包发多条，收集等量的结果码。返回 [(E码, 详情), ...]。

        为什么能这么干：板子严格串行执行，结果包的顺序 == 发送顺序。
        结果包不带票号，所以只能按 FIFO 配对——这也是为什么一旦
        条数对不上就必须整窗重发（见 JobRunner）。

        一包多条能把「每包约 300ms 的往返开销」摊薄：发 6 条只付一次。
        实测同一包内 3 条指令的结果几乎同时返回。

        上限受两件事约束：
          - UDP 单包 ≤1472B（调用方保证）
          - 板子队列深度（超发丢行，实测 ≥20 没事，保守取 6）
        """
        if not self.sock:
            return [("", "未连接")] * len(lines)
        if not lines:
            return []

        payload = b"".join((ln + "\r").encode("utf-8") for ln in lines)
        try:
            self.sock.sendto(payload, (self.ip, self.port))
        except OSError as e:
            self.last_error = str(e)
            log.error(SRC_ERR, f"批量发送失败：{e}")
            return [("", str(e))] * len(lines)

        self.sent += len(lines)
        log.debug(SRC_UDP_TX, f"[批量 {len(lines)} 条] " + " | ".join(l[:24] for l in lines),
                  raw=payload)

        results: list[tuple[str, str]] = []
        detail = ""
        # 条数越多，需要的总时间越长（每条运动都要跑完）
        deadline = time.time() + timeout + per_line_extra * len(lines)

        while time.time() < deadline and len(results) < len(lines):
            try:
                data, _ = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("utf-8", errors="replace")
            log.debug(SRC_UDP_RX, text.replace("\r", "\\r").replace("\n", "\\n"), raw=data)
            for part in text.replace("\n", "\r").split("\r"):
                p = part.strip()
                if not p:
                    continue
                if _is_result_code(p):
                    results.append((p, detail))
                    detail = ""
                    if _is_ok_code(p):
                        self.ok += 1
                    else:
                        self.failed += 1
                        self.last_error = p
                    if len(results) >= len(lines):
                        break
                elif p.startswith("queued"):
                    continue
                else:
                    # echo 或详情行；echo 和指令本身相同时忽略
                    if p not in lines:
                        detail = p

        # 没凑齐的补空，让调用方知道这几条没确认
        while len(results) < len(lines):
            results.append(("", ""))
            self.failed += 1

        bad = [(i, c) for i, (c, _) in enumerate(results) if not (c == "E000" or c == "B000")]
        if bad:
            log.warn(SRC_UDP_RX,
                     f"批量 {len(lines)} 条中有 {len(bad)} 条异常："
                     + ", ".join(f"#{i}:{c or '超时'}" for i, c in bad[:6]))

        return results

    def snapshot(self) -> dict:
        return {
            "ip": self.ip,
            "port": self.port,
            "connected": self.sock is not None,
            "sent": self.sent,
            "ok": self.ok,
            "failed": self.failed,
            "last_error": self.last_error,
        }


# ══════════════════════════════════════════════════════════
# 下发任务
# ══════════════════════════════════════════════════════════


@dataclass
class JobState:
    running: bool = False
    total: int = 0
    done: int = 0
    errors: int = 0
    current: str = ""
    message: str = "空闲"
    started_at: float = 0.0
    finished_at: float = 0.0
    aborted: bool = False
    log: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobRunner:
    """把 G-code 发给板子。

    两种策略：
      batch_size=1  逐行发，一发一等。稳，但每行付一次约 300ms 的往返开销。
      batch_size>1  一包发 N 条，等 N 个结果码。把往返开销摊薄到 1/N。

    为什么批量是安全的：板子严格串行执行，结果包顺序 == 发送顺序。
    为什么批量要小心：结果包不带票号，条数对不上就配对错位。
      所以只要有一窗没凑齐，就停下整窗重发（G90 绝对坐标，重发幂等）。

    带延时的行（delays[i] > 0）必须独占一窗——延时要在「这条执行完」
    之后才生效，夹在批量里会打乱时序。
    """

    def __init__(self, link: BoardLink, batch_size: int = 1):
        self.link = link
        self.state = JobState()
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()
        # 一窗几条。1 = 逐行；>1 = 批量。
        # 上限受 UDP 单包 1472B 和板子队列深度约束，6 是保守值。
        self.batch_size = max(1, min(8, batch_size))

    def start(self, gcode: str, *, skip_comments: bool = True,
              delays: list[float] | None = None,
              dwell_unit: str = "ms") -> tuple[bool, str]:
        """开始发送。

        delays 是「每行之后上位机要等多少秒」，来自编译器在 dwell="host"
        模式下的输出。长度应与 gcode 的行数一致（含被跳过的注释行）。
        """
        if self.state.running:
            return False, "已经在发送了"

        # G4 的 P 单位随板子不同（H5 秒 / R7-Marlin 毫秒），
        # 计算等待超时时必须知道是哪一种，否则 G4 P10 会在 5 秒就误判超时。
        self._dwell_unit = dwell_unit

        raw_lines = gcode.splitlines()
        lines: list[str] = []
        line_delays: list[float] = []
        for idx, raw in enumerate(raw_lines):
            s = raw.split(";")[0].strip()
            d = delays[idx] if delays and idx < len(delays) else 0.0
            if not s:
                continue
            if skip_comments and s.startswith(";"):
                continue
            if len(s) > MAX_LINE:
                return False, f"有行超过 {MAX_LINE} 字节：{s[:40]}…"
            lines.append(s)
            line_delays.append(d)

        if not lines:
            return False, "没有可发送的指令"

        total_dwell = sum(line_delays)
        self.state = JobState(running=True, total=len(lines), started_at=time.time())
        self._stop.clear()
        msg = f"开始发送 {len(lines)} 行"
        if total_dwell > 0:
            msg += f"（含上位机延时 {total_dwell:.1f}s）"
        if self.batch_size > 1:
            msg += f"，每包 {self.batch_size} 条"
        log.info(SRC_JOB, msg + f" → {self.link.ip}:{self.link.port}")
        self.thread = threading.Thread(target=self._run, args=(lines, line_delays), daemon=True)
        self.thread.start()
        return True, msg

    def stop(self):
        self._stop.set()
        self.state.aborted = True
        log.warn(SRC_JOB, "收到中止请求")

    def _run(self, lines: list[str], delays: list[float] | None = None):
        st = self.state
        last_report = 0
        n = len(lines)
        delays = delays or [0.0] * n

        i = 0
        while i < n:
            if self._stop.is_set():
                st.message = f"已中止（第 {i}/{st.total} 行）"
                log.warn(SRC_JOB, st.message)
                break

            # ── 组一个窗口 ──
            # 带延时的行独占一窗（延时必须在它执行完之后才开始算）
            win_end = i + 1
            if self.batch_size > 1 and delays[i] <= 0:
                while (win_end < n and win_end - i < self.batch_size
                       and delays[win_end] <= 0):
                    win_end += 1
            win = lines[i:win_end]

            st.current = win[0]

            if len(win) == 1:
                # 单条：走原来的精确路径
                timeout = 30.0
                up = win[0].upper()
                if up.startswith("G4"):
                    try:
                        val = float(up.split("P")[1].split()[0])
                        unit = getattr(self, "_dwell_unit", "ms")
                        sec = val if unit == "s" else val / 1000.0
                        timeout = max(5.0, sec + 5)
                    except Exception:
                        pass
                results = [self.link.ask(win[0], timeout=timeout)]
            else:
                results = self.link.ask_many(win, timeout=15.0, per_line_extra=1.5)

            # ── 判定这一窗 ──
            failed = [(k, c) for k, (c, _) in enumerate(results) if not (c == "E000" or c == "B000")]
            if not failed:
                st.done += len(win)
                i = win_end
            elif all(c == "" for _, c in failed) and len(win) > 1:
                # 整窗都没确认 → 降级为逐条重发（不惩罚计数，避免重复计错）
                log.warn(SRC_JOB, f"窗口 {i+1}..{i+len(win)} 未确认，降级逐条重发")
                for k, ln in enumerate(win):
                    if self._stop.is_set():
                        break
                    st.current = ln
                    c1, _ = self.link.ask(ln, timeout=60.0)
                    if (c1 == "E000" or c1 == "B000"):
                        st.done += 1
                    else:
                        st.errors += 1
                        m = f"第 {i + k + 1} 行 {c1 or '超时'} | {ln[:40]}"
                        st.log.append(m)
                        log.error(SRC_ERR, m)
                        if len(st.log) > 200:
                            st.log.pop(0)
                i = win_end
            else:
                # 部分失败：逐条记错
                for k, (c, detail) in enumerate(results):
                    if c == "E000":
                        st.done += 1
                        continue
                    st.errors += 1
                    extra = "" if (not detail or detail.strip() == win[k].strip()) else f" | {detail[:40]}"
                    m = f"第 {i + k + 1} 行 {c or '超时'} | {win[k][:40]}{extra}"
                    st.log.append(m)
                    log.error(SRC_ERR, m)
                    if len(st.log) > 200:
                        st.log.pop(0)
                i = win_end

            # ── 严重错误直接停 ──
            severe = [c for c, _ in results if c in ("E013", "E014", "E015", "E012")]
            if severe:
                st.message = f"中止：{severe[0]}（安全类错误）"
                log.error(SRC_ERR, st.message)
                break

            # ── 进度 ──
            if st.done - last_report >= 50:
                last_report = st.done
                log.info(SRC_JOB, f"进度 {st.done}/{st.total}  （{st.done*100//max(1,st.total)}%）")

            # ── 上位机延时（dwell="host"）──
            # 窗口里只有最后一行可能有延时（带延时的行独占一窗）
            d = delays[win_end - 1]
            if d > 0:
                deadline = time.time() + d
                while time.time() < deadline:
                    if self._stop.is_set():
                        break
                    time.sleep(min(0.02, max(0.0, deadline - time.time())))

        st.running = False
        st.finished_at = time.time()
        if not st.aborted:
            st.message = (
                f"完成：{st.done}/{st.total} 行成功，{st.errors} 个错误"
                if st.errors else f"完成：{st.done} 行全部成功"
            )
            elapsed = st.finished_at - st.started_at
            (log.error if st.errors else log.info)(
                SRC_JOB, f"{st.message}（耗时 {elapsed:.1f}s）"
            )

    def snapshot(self) -> dict:
        st = self.state
        elapsed = 0.0
        if st.started_at:
            end = st.finished_at or time.time()
            elapsed = end - st.started_at
        return {
            "running": st.running,
            "total": st.total,
            "done": st.done,
            "errors": st.errors,
            "current": st.current,
            "message": st.message,
            "progress": (st.done / st.total) if st.total else 0.0,
            "elapsed": round(elapsed, 1),
            "log": st.log[-30:],
        }


# ══════════════════════════════════════════════════════════
# 单例
# ══════════════════════════════════════════════════════════

_mock: MockBoard | None = None
_link: BoardLink | None = None
_job: JobRunner | None = None
_lock = threading.Lock()


def get_mock() -> MockBoard:
    global _mock
    with _lock:
        if _mock is None:
            _mock = MockBoard()
        return _mock


def get_link(ip: str = DEFAULT_IP, port: int = DEFAULT_PORT,
             batch_size: int | None = None) -> BoardLink:
    global _link, _job
    with _lock:
        if _link is None or _link.ip != ip or _link.port != port:
            if _link:
                _link.close()
            _link = BoardLink(ip, port)
            _job = JobRunner(_link, batch_size=batch_size or 1)
        elif batch_size is not None and _job is not None:
            # 允许运行中调整窗口大小（不影响正在跑的任务，下次 start 生效）
            _job.batch_size = max(1, min(8, batch_size))
        return _link


def get_job(ip: str = DEFAULT_IP, port: int = DEFAULT_PORT,
            batch_size: int | None = None) -> JobRunner:
    get_link(ip, port, batch_size)
    return _job  # type: ignore[return-value]
