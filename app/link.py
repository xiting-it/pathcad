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

# ══════════════════════════════════════════════════════════
# 通用
# ══════════════════════════════════════════════════════════

DEFAULT_IP = "192.168.0.102"
DEFAULT_PORT = 5000
MOCK_IP = "127.0.0.1"

MAX_LINE = 128
MAX_PACKET = 1472


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
            return False, f"端口 {self.state.port} 绑定失败：{e}（可能被占用或已有真机连接）"

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
        return True, f"模拟板已启动，监听 {MOCK_IP}:{self.state.port}"

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

        results: list[tuple[str, float]] = []  # (E码, 完成时刻)
        for raw in lines:
            line = raw.split(";")[0].strip()
            if not line:
                continue
            code, finish = self._exec(line)
            results.append((code, finish))

        with self.state.lock:
            pending = len([q for q in self.state.queue if q[1] > time.time()])
        if results:
            msg = "\r".join(f"queued #{i + 1} ({pending} pending)" for i, _ in enumerate(results))
            self._reply(addr, msg)
        # echo 包
        self._reply(addr, data.decode("utf-8", errors="replace"))

        # 结果包：等到这条指令真正执行完再回
        for code, finish in results:
            delay = max(0.0, finish - time.time())
            if delay <= 0.001:
                self._reply(addr, code)
            else:
                threading.Timer(delay, self._send_result, args=(addr, code)).start()

    def _send_result(self, addr, code: str):
        self._reply(addr, code)

    def _reply(self, addr, text: str):
        if not self.sock:
            return
        try:
            self.sock.sendto((text + "\r").encode("utf-8"), addr)
        except OSError:
            pass

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
            ch = 0
            lv = 0
            for tok in up.split():
                try:
                    if tok.startswith("U"):
                        ch = int(float(tok[1:]))
                    elif tok.startswith("V"):
                        lv = int(float(tok[1:]))
                except ValueError:
                    pass
            if not (0 <= ch <= 7):
                st.errors += 1
                return ("E010", now)
            with st.lock:
                st.io_out[ch] = lv
                st.io_events.append((time.time() - st.started_at, ch, lv))
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
            ms = 0.0
            for tok in up.split():
                try:
                    if tok.startswith("P"):
                        ms = float(tok[1:])
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
            # 用 M114 探活
            code, detail = self.ask("M114", timeout=2.0)
            if code == "E000":
                return True, f"已连接 {self.ip}:{self.port}（{detail or 'ok'}）"
            if code:
                return True, f"已连接 {self.ip}:{self.port}，探活返回 {code}"
            return False, f"连不上 {self.ip}:{self.port}（无回包）。检查网线、IP 网段、板子是否上电。"
        except OSError as e:
            return False, f"网络错误：{e}"

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def ask(self, line: str, timeout: float = 5.0) -> tuple[str, str]:
        """发一行，等 E 码。返回 (E码, 详情文本)。超时返回 ('', '')。"""
        if not self.sock:
            return "", "未连接"
        if len(line) > MAX_LINE:
            return "E022", "行超长"
        try:
            self.sock.sendto((line + "\r").encode("utf-8"), (self.ip, self.port))
        except OSError as e:
            self.last_error = str(e)
            return "", str(e)
        self.sent += 1
        deadline = time.time() + timeout
        detail = ""
        while time.time() < deadline:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            text = data.decode("utf-8", errors="replace")
            for part in text.replace("\n", "\r").split("\r"):
                p = part.strip()
                if not p:
                    continue
                if len(p) == 4 and p[0] == "E" and p[1:].isdigit():
                    if p == "E000":
                        self.ok += 1
                    else:
                        self.failed += 1
                        self.last_error = p
                    return p, detail
                # queued #N / echo / 详情，都先记下来继续等
                if not p.startswith("queued"):
                    detail = p
        return "", ""

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
    """把 G-code 逐行发给板子，一发一等。"""

    def __init__(self, link: BoardLink):
        self.link = link
        self.state = JobState()
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, gcode: str, *, skip_comments: bool = True) -> tuple[bool, str]:
        if self.state.running:
            return False, "已经在发送了"
        lines = []
        for raw in gcode.splitlines():
            s = raw.split(";")[0].strip()
            if not s:
                continue
            if skip_comments and s.startswith(";"):
                continue
            if len(s) > MAX_LINE:
                return False, f"有行超过 {MAX_LINE} 字节：{s[:40]}…"
            lines.append(s)
        if not lines:
            return False, "没有可发送的指令"

        self.state = JobState(running=True, total=len(lines), started_at=time.time())
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, args=(lines,), daemon=True)
        self.thread.start()
        return True, f"开始发送 {len(lines)} 行"

    def stop(self):
        self._stop.set()
        self.state.aborted = True

    def _run(self, lines: list[str]):
        st = self.state
        for i, line in enumerate(lines):
            if self._stop.is_set():
                st.message = f"已中止（第 {i}/{st.total} 行）"
                break
            st.current = line
            timeout = 30.0
            up = line.upper()
            if up.startswith("G4"):
                try:
                    ms = float(up.split("P")[1].split()[0])
                    timeout = max(5.0, ms / 1000.0 + 5)
                except Exception:
                    pass
            code, detail = self.link.ask(line, timeout=timeout)
            if code == "E000":
                st.done += 1
            elif code == "":
                st.errors += 1
                st.log.append(f"第 {i + 1} 行超时 | {line[:40]}")
                if len(st.log) > 200:
                    st.log.pop(0)
            else:
                st.errors += 1
                # detail 是 echo 回显（与 line 相同），只在不重复时附上
                extra = "" if (not detail or detail.strip() == line.strip()) else f" | {detail[:40]}"
                st.log.append(f"第 {i + 1} 行 {code} | {line[:40]}{extra}")
                if len(st.log) > 200:
                    st.log.pop(0)
                # 严重错误直接停
                if code in ("E013", "E014", "E015", "E012"):
                    st.message = f"中止：{code}（安全类错误）"
                    break

        st.running = False
        st.finished_at = time.time()
        if not st.aborted:
            st.message = (
                f"完成：{st.done}/{st.total} 行成功，{st.errors} 个错误"
                if st.errors else f"完成：{st.done} 行全部成功"
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


def get_link(ip: str = DEFAULT_IP, port: int = DEFAULT_PORT) -> BoardLink:
    global _link, _job
    with _lock:
        if _link is None or _link.ip != ip or _link.port != port:
            if _link:
                _link.close()
            _link = BoardLink(ip, port)
            _job = JobRunner(_link)
        return _link


def get_job(ip: str = DEFAULT_IP, port: int = DEFAULT_PORT) -> JobRunner:
    get_link(ip, port)
    return _job  # type: ignore[return-value]
